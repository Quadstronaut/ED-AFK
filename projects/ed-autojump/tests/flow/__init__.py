"""Flow tests package + shared test double."""


class FakeSender:
    """Minimal Sender stand-in: records pressed actions; raises KeyError for any
    action listed in `unbound` (to exercise the steps' fail-on-missing-bind path).

    press(action) records `action`. key_down(action) / key_up(action) record
    f"{action}:down" / f"{action}:up" so tests assert the down/up pair order
    for hold-until-event semantics."""

    def __init__(self, unbound=()):
        self.events: list[str] = []
        self._unbound = set(unbound)

    def press(self, action, *, hold=0.05):
        if action in self._unbound:
            raise KeyError(action)
        self.events.append(action)

    def key_down(self, action):
        if action in self._unbound:
            raise KeyError(action)
        self.events.append(f"{action}:down")

    def key_up(self, action):
        if action in self._unbound:
            raise KeyError(action)
        self.events.append(f"{action}:up")

    def actions(self):
        return list(self.events)
