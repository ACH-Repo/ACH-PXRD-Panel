"""A skeletal structure from a SMILES: the atoms and bonds to draw.

RDKit lays the molecule out in 2D (CoordGen when it has it, which draws
rings and chains the way a chemist would), and this hands back plain data -
elements, positions, hydrogens, charges, bond orders - which the plot draws
itself with its own painter (`ui/plot.py`). Drawn that way a structure is
VECTOR in every export, its bond width and label size are settings, and its
labels can stay upright when the structure is rotated.
RDKit's own drawing would give a picture, none of that.

The layout is STORED with the figure, so a session with a structure opens
without RDKit; RDKit is needed only to make a new one. It is optional: see
`available`.

UI-free: no Qt here.
"""

import math
import re

#: The characters a SMILES is made of. Anything else - a space, a comma in
#: running text - means the clipboard holds words, not a structure.
_SMILES = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#$:/\\%.*]+$")


def available():
    """True when RDKit is there to lay a structure out."""
    try:
        from rdkit import Chem  # noqa: F401
        from rdkit.Chem import rdDepictor  # noqa: F401
    except Exception:
        return False
    return True


#: One SMILES token: a bracket atom, a two-letter halogen, an organic-
#: subset atom (aromatic in lower case), a bond, a branch, a ring closure.
_TOKEN = re.compile(r"\[[^\[\]]+\]|Br|Cl|[BCNOPSFI]|[bcnops]|\*"
                    r"|[-=#$:/\\.]|[()]|%\d\d|\d")
_ATOM = re.compile(r"\[[^\[\]]+\]|Br|Cl|[BCNOPSFI]|[bcnops]|\*")


def plausible_smiles(text):
    """True when `text` reads as a SMILES of two atoms or more, WITHOUT
    RDKit: every character belongs to a token of the grammar, brackets and
    branches balance, and every ring-closure number is opened and closed.
    Not proof - RDKit alone parses it - but enough to tell "O=C(O)CCCCC(O)=O"
    from a word, which is what the program must know to say RDKit is
    missing (pasting one otherwise did nothing visible)."""
    text = str(text or "").strip()
    if not text or "\n" in text or not _SMILES.match(text):
        return False
    tokens = _TOKEN.findall(text)
    if "".join(tokens) != text:
        return False
    depth, rings, atoms = 0, {}, 0
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth < 0:
                return False
        elif token.isdigit() or token.startswith("%"):
            rings[token] = rings.get(token, 0) + 1
        elif _ATOM.fullmatch(token):
            atoms += 1
    return (depth == 0 and atoms >= 2
            and all(count % 2 == 0 for count in rings.values()))


def looks_like_smiles(text):
    """True when `text` is one line that parses as a molecule of at least
    two atoms. A single atom ("C", "N") is far more likely a letter somebody
    copied than a structure."""
    text = str(text or "").strip()
    if not text or "\n" in text or not _SMILES.match(text):
        return False
    molecule = _parse(text)
    return molecule is not None and molecule.GetNumAtoms() >= 2


def _parse(smiles):
    """The RDKit molecule, or None - quietly: RDKit prints a parse error for
    every string that is not a SMILES, and most pasted text is not."""
    if not available():
        return None
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")
    try:
        return Chem.MolFromSmiles(str(smiles))
    except Exception:
        return None
    finally:
        RDLogger.EnableLog("rdApp.*")


def layout(smiles):
    """`{"atoms": [...], "bonds": [...]}` for a SMILES, or None.

    Atoms: `{"el", "x", "y", "h", "charge", "show"}`, positions in BOND
    LENGTHS (the average bond is 1) with y UP, centred on the origin.
    `show` is False for a carbon that is a vertex, as in a skeletal
    formula. Bonds: `{"a", "b", "order", "ring", "stereo"}`, `ring` the
    centre of the smallest ring a double bond is in (its second line is
    drawn towards it), or None; `stereo` "wedge" (towards the viewer) or
    "hash" (away) for a bond RDKit wedges at a stereocentre of the SMILES
    (`@` / `@@`), with `a` the stereocentre - the narrow end - or None.
    """
    molecule = _parse(smiles)
    if molecule is None or molecule.GetNumAtoms() == 0:
        return None
    from rdkit import Chem
    from rdkit.Chem import rdDepictor
    try:
        rdDepictor.SetPreferCoordGen(True)
    except Exception:
        pass
    rdDepictor.Compute2DCoords(molecule)
    # Which single bond at each stereocentre is drawn as a wedge or a hash,
    # chosen by RDKit from the layout. It puts the stereocentre first in
    # each such bond.
    try:
        Chem.WedgeMolBonds(molecule, molecule.GetConformer())
    except Exception:
        pass
    try:
        Chem.Kekulize(molecule, clearAromaticFlags=True)
    except Exception:
        pass
    conformer = molecule.GetConformer()
    points = [conformer.GetAtomPosition(i)
              for i in range(molecule.GetNumAtoms())]
    lengths = [math.hypot(points[b.GetBeginAtomIdx()].x
                          - points[b.GetEndAtomIdx()].x,
                          points[b.GetBeginAtomIdx()].y
                          - points[b.GetEndAtomIdx()].y)
               for b in molecule.GetBonds()]
    unit = (sum(lengths) / len(lengths)) if lengths else 1.0
    unit = unit or 1.0
    cx = sum(p.x for p in points) / len(points)
    cy = sum(p.y for p in points) / len(points)
    atoms = []
    for atom, point in zip(molecule.GetAtoms(), points):
        symbol = atom.GetSymbol()
        charge = atom.GetFormalCharge()
        shown = (symbol != "C" or charge != 0 or atom.GetDegree() == 0
                 or atom.GetIsotope() != 0)
        atoms.append({"el": symbol, "x": (point.x - cx) / unit,
                      "y": (point.y - cy) / unit,
                      "h": int(atom.GetTotalNumHs()), "charge": int(charge),
                      "show": bool(shown)})
    _separate_fragments(molecule, atoms)
    rings = [list(ring) for ring in molecule.GetRingInfo().AtomRings()]
    bonds = []
    for bond in molecule.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        order = {1.0: 1, 2.0: 2, 3.0: 3}.get(bond.GetBondTypeAsDouble(), 1)
        ring = None
        if order == 2:
            holding = [r for r in rings if a in r and b in r]
            if holding:
                smallest = min(holding, key=len)
                ring = [sum(atoms[i]["x"] for i in smallest) / len(smallest),
                        sum(atoms[i]["y"] for i in smallest) / len(smallest)]
        stereo = None
        if order == 1:
            stereo = {Chem.BondDir.BEGINWEDGE: "wedge",
                      Chem.BondDir.BEGINDASH: "hash"}.get(bond.GetBondDir())
        bonds.append({"a": a, "b": b, "order": order, "ring": ring,
                      "stereo": stereo})
    return {"atoms": atoms, "bonds": bonds}


def _separate_fragments(molecule, atoms):
    """Put the pieces of a salt or a solvate side by side, in the order the
    SMILES names them: RDKit's layout can drop a counter-ion onto the atom
    it balances (Na+ on a carboxylate's O-). A bond length and a half
    apart, level with the first piece; then everything centred again."""
    from rdkit import Chem
    pieces = Chem.GetMolFrags(molecule)
    if len(pieces) < 2:
        return
    right = None
    level = None
    for piece in pieces:
        xs = [atoms[i]["x"] for i in piece]
        ys = [atoms[i]["y"] for i in piece]
        middle = (min(ys) + max(ys)) / 2.0
        if right is None:
            right, level = max(xs), middle
            continue
        shift_x = right + 1.5 - min(xs)
        shift_y = level - middle
        for i in piece:
            atoms[i]["x"] += shift_x
            atoms[i]["y"] += shift_y
        right = max(atoms[i]["x"] for i in piece)
    cx = sum(a["x"] for a in atoms) / len(atoms)
    cy = sum(a["y"] for a in atoms) / len(atoms)
    for atom in atoms:
        atom["x"] -= cx
        atom["y"] -= cy


def label_of(atom, hydrogens_left=False):
    """An atom's label in the figure markup: `OH`, `H_{2}N`, `N^{+}`."""
    text = atom["el"]
    count = int(atom.get("h", 0))
    if count:
        hydrogens = "H" if count == 1 else "H_{%d}" % count
        text = hydrogens + text if hydrogens_left else text + hydrogens
    charge = int(atom.get("charge", 0))
    if charge:
        sign = "+" if charge > 0 else "\u2212"
        text += "^{%s%s}" % ("" if abs(charge) == 1 else abs(charge), sign)
    return text


def mirrored_layout(atoms, bonds, horizontal=True):
    """A drawn structure mirrored left-right (or top-bottom) about its
    middle, as NEW lists: `(atoms, bonds)`. Wedges and hashes swap, so it
    is the same molecule drawn the other way round and not its mirror
    image. Labels stay upright: they are drawn at the atoms' places,
    never mirrored themselves."""
    key = "x" if horizontal else "y"
    values = [float(atom.get(key, 0.0)) for atom in atoms]
    if not values:
        return list(atoms), list(bonds)
    middle = (min(values) + max(values)) / 2.0
    new_atoms = []
    for atom in atoms:
        moved = dict(atom)
        moved[key] = 2.0 * middle - float(atom.get(key, 0.0))
        new_atoms.append(moved)
    swap = {"wedge": "hash", "hash": "wedge"}
    index = 0 if horizontal else 1
    new_bonds = []
    for bond in bonds:
        turned = dict(bond)
        if turned.get("stereo") in swap:
            turned["stereo"] = swap[turned["stereo"]]
        # A ring's double bond keeps the centre of its ring, which says
        # which side its inner line is on: it mirrors with the atoms, or
        # the line is drawn outside the ring.
        ring = turned.get("ring")
        if ring and len(ring) == 2:
            ring = [float(ring[0]), float(ring[1])]
            ring[index] = 2.0 * middle - ring[index]
            turned["ring"] = ring
        new_bonds.append(turned)
    return new_atoms, new_bonds
