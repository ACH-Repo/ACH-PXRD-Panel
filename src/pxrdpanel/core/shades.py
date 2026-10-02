"""Shades of one colour for a stack of curves: dark to light, one hue.

A stacked series of samples reads well in one colour, darkest at the top:
F3 gives the selected curves such a set.
A shade is the base colour mixed towards black (a negative amount) or
towards white (a positive one), so the hue stays and only the lightness
moves; the base itself is amount 0.

UI-free: colours are "#rrggbb" strings.
"""


def _rgb(name):
    name = str(name).lstrip("#")
    if len(name) == 3:
        name = "".join(c * 2 for c in name)
    if len(name) != 6:
        raise ValueError("not a colour: {!r}".format(name))
    return tuple(int(name[i:i + 2], 16) for i in (0, 2, 4))


def _name(rgb):
    return "#{:02x}{:02x}{:02x}".format(
        *(int(round(min(max(c, 0.0), 255.0))) for c in rgb))


def shade(base, amount):
    """`base` mixed towards white by `amount` (0..1) or towards black by
    -`amount` (-1..0)."""
    amount = min(max(float(amount), -1.0), 1.0)
    target = 255.0 if amount > 0 else 0.0
    share = abs(amount)
    return _name(c + (target - c) * share for c in _rgb(base))


def shades(base, count, darkest=-0.4, lightest=0.5):
    """`count` colours from `darkest` to `lightest` (amounts as in
    `shade`), evenly spaced; one curve gets the middle of the range."""
    count = int(count)
    if count <= 0:
        return []
    if count == 1:
        return [shade(base, (darkest + lightest) / 2.0)]
    step = (lightest - darkest) / (count - 1)
    return [shade(base, darkest + step * k) for k in range(count)]


def stack_order(scans):
    """The scans from the TOP of the stack down: by offset, highest first,
    ties in the order given."""
    indexed = list(enumerate(scans))
    indexed.sort(key=lambda pair: (-float(getattr(pair[1], "offset", 0.0)
                                          or 0.0), pair[0]))
    return [scan for _k, scan in indexed]
