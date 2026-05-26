"""Flow tests package + shared test double."""


class FakeSender:
    """Minimal Sender stand-in: records pressed actions; raises KeyError for any
    action listed in `unbound` (to exercise the steps' fail-on-missing-bind path)."""

    def __init__(self, unbound=()):
        self.events: list[str] = []
        self._unbound = set(unbound)

    def press(self, action, *, hold=0.05):
        if action in self._unbound:
            raise KeyError(action)
        self.events.append(action)

    def actions(self):
        return list(self.events)
