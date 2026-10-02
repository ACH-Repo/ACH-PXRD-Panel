"""Undo and redo over object properties.

Built in from the first round rather than added later, because everything in
this program is "change a property of an object" - an offset dragged, a colour
picked, a marker line moved, a scan hidden - and a transform system that has to
grow an undo stack afterwards is a rewrite of the transform system.

**A gesture is one entry.** Dragging a scan emits a property change per mouse
move; thirty of them in a drag must not be thirty undo steps. So a command
knows how to `merge` with the one before it, and the stack merges while a
gesture is live (`push(..., merge=True)`) and stops merging when the gesture
ends (`seal`). That is the same rule Blender uses: one Ctrl+Z per action, not
per frame.

UI-free: an object is anything with settable attributes, so this is testable
without a window.
"""


class Command(object):
    """One undoable change. Subclasses implement `undo` and `redo`."""

    #: What the menu entry says: "Undo move scan".
    label = "change"

    def undo(self):
        raise NotImplementedError

    def redo(self):
        raise NotImplementedError

    def merge(self, other):
        """Absorb `other`, which happened after this one. True if absorbed."""
        return False


class SetProps(Command):
    """Set named attributes on objects, remembering what they were.

    Takes a list of `(obj, name, new_value)` and reads the old values itself,
    so a caller cannot forget to snapshot one. Objects are held by reference:
    an undo stack over deleted objects would resurrect them half-connected,
    so deletion is its own command and deleted objects are kept alive by it.
    """

    def __init__(self, changes, label="change"):
        self.label = label
        self._entries = []
        for obj, name, new in changes:
            self._entries.append((obj, name, getattr(obj, name), new))
        self.redo()

    def undo(self):
        for obj, name, old, _new in self._entries:
            setattr(obj, name, old)

    def redo(self):
        for obj, name, _old, new in self._entries:
            setattr(obj, name, new)

    def merge(self, other):
        """Absorb a later change to the SAME properties of the same objects.

        Same set, same order, same attribute names: a drag is one command
        whose end value keeps moving. A different set means a different
        action, which deserves its own step even mid-gesture.
        """
        if not isinstance(other, SetProps) or other.label != self.label:
            return False
        if len(other._entries) != len(self._entries):
            return False
        merged = []
        for mine, theirs in zip(self._entries, other._entries):
            if mine[0] is not theirs[0] or mine[1] != theirs[1]:
                return False
            merged.append((mine[0], mine[1], mine[2], theirs[3]))
        self._entries = merged
        return True


class CallCommand(Command):
    """An undoable action given as two callables, for the odd case that is
    not a property set (adding an object, reordering a list)."""

    def __init__(self, do, undo, label="change"):
        self.label = label
        self._do, self._undo = do, undo
        self._do()

    def undo(self):
        self._undo()

    def redo(self):
        self._do()


class GroupCommand(Command):
    """Several commands as ONE step - a sweep across the outliner's boxes,
    which shows some scans and puts others on the plot."""

    def __init__(self, label="change"):
        self.label = label
        self.commands = []

    def undo(self):
        for command in reversed(self.commands):
            command.undo()

    def redo(self):
        for command in self.commands:
            command.redo()


class UndoStack(object):
    """The usual two lists, plus merging and a change signal.

    `on_change` is a plain callable rather than a Qt signal, so the stack
    stays UI-free; the window hands it a method that refreshes the menu and
    redraws.
    """

    def __init__(self, on_change=None, limit=200):
        self._done = []
        self._undone = []
        self._limit = int(limit)
        self._open = False          # a gesture is live, so merging is allowed
        self._group = None          # commands being gathered into one step
        self.on_change = on_change

    # ------------------------------------------------------------- gestures
    def begin(self):
        """Start a gesture: the commands pushed until `seal` may merge."""
        self._open = True

    def seal(self):
        """End a gesture. The next push starts a new undo step."""
        self._open = False

    def begin_group(self, label="change"):
        """Gather every command pushed from now on into ONE step."""
        if self._group is None:
            self._group = GroupCommand(label)

    def end_group(self):
        """Close the gathering; what it gathered is one step (if any)."""
        group, self._group = self._group, None
        if group is None or not group.commands:
            return None
        if len(group.commands) == 1:
            group = group.commands[0]
        self._done.append(group)
        del self._done[:max(0, len(self._done) - self._limit)]
        self._undone = []
        self._changed()
        return group

    # --------------------------------------------------------------- stack
    def push(self, command):
        """Add a command that has ALREADY been applied.

        Commands apply themselves in `__init__` (see `SetProps`), because a
        command that is built and then separately applied is one somebody
        forgets to apply.
        """
        if self._group is not None:
            # Already applied; kept for the one step `end_group` makes.
            self._group.commands.append(command)
            self._undone = []
            self._changed()
            return command
        if self._open and self._done and self._done[-1].merge(command):
            self._undone = []
            self._changed()
            return self._done[-1]
        self._done.append(command)
        del self._done[:max(0, len(self._done) - self._limit)]
        self._undone = []
        self._changed()
        return command

    def set_props(self, changes, label="change"):
        """The common case: `[(obj, name, value), ...]` as one step."""
        changes = [c for c in changes if getattr(c[0], c[1]) != c[2]]
        if not changes:
            return None
        return self.push(SetProps(changes, label))

    def undo(self):
        if not self._done:
            return False
        command = self._done.pop()
        command.undo()
        self._undone.append(command)
        self._open = False
        self._changed()
        return True

    def redo(self):
        if not self._undone:
            return False
        command = self._undone.pop()
        command.redo()
        self._done.append(command)
        self._open = False
        self._changed()
        return True

    def clear(self):
        self._done, self._undone, self._open = [], [], False
        self._changed()

    # --------------------------------------------------------------- state
    def can_undo(self):
        return bool(self._done)

    def can_redo(self):
        return bool(self._undone)

    def undo_label(self):
        return self._done[-1].label if self._done else ""

    def redo_label(self):
        return self._undone[-1].label if self._undone else ""

    def depth(self):
        return len(self._done)

    def _changed(self):
        if self.on_change is not None:
            self.on_change()
