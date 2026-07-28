import re

class EvidenceBundle:
    def __init__(self, items):
        self.items = items

    @classmethod
    def build(cls, timeline):
        return cls([{"id": "EV-%03d" % (i + 1), "record": r} for i, r in enumerate(timeline)])

    def find(self, service=None, level=None, body_regex=None, attr=None):
        rx = re.compile(body_regex) if body_regex else None
        out = []
        for it in self.items:
            r = it["record"]
            if service and r.service != service: continue
            if level and r.level != level: continue
            if rx and not rx.search(r.body): continue
            if attr and attr not in r.attrs: continue
            out.append(it)
        return out

    def by_id(self, ev_id):
        for it in self.items:
            if it["id"] == ev_id: return it
        return None

    def to_json(self):
        return [{"id": it["id"], **it["record"].to_dict()} for it in self.items]
