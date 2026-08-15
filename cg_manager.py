import os
import json

def is_system_api(dalvik_path):
    """Prüft, ob eine Klasse zum Android/Java-Framework gehört und nicht gepatcht werden kann."""
    prefixes = ["Ljava/", "Landroid/", "Landroidx/", "Lkotlin/", "Ldalvik/", "Ljavax/"]
    return any(dalvik_path.startswith(p) for p in prefixes)

class MethodNode:
    def __init__(self, filepath, signature):
        self.id = f"{filepath}|{signature}"
        self.filepath = filepath
        self.signature = signature
        # Wir speichern hier nur die Referenz-IDs (Strings) zu anderen Nodes
        self.callers = set()
        self.callees = set()

    def to_dict(self):
        return {
            "filepath": self.filepath,
            "signature": self.signature,
            "callers": list(self.callers),
            "callees": list(self.callees)
        }

    @classmethod
    def from_dict(cls, data):
        node = cls(data["filepath"], data["signature"])
        node.callers = set(data["callers"])
        node.callees = set(data["callees"])
        return node

class CallGraphManager:
    def __init__(self):
        self.nodes = {}
        self.roots = set()

    def add_node(self, filepath, signature):
        """Erzeugt eine Node, falls sie noch nicht existiert. Verhindert Duplikate."""
        node_id = f"{filepath}|{signature}"
        if node_id not in self.nodes:
            self.nodes[node_id] = MethodNode(filepath, signature)
        return self.nodes[node_id]

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def add_edge(self, caller_filepath, caller_sig, callee_filepath, callee_sig):
        """Verbindet zwei Nodes miteinander (Referenz-Mapping)."""
        caller = self.add_node(caller_filepath, caller_sig)
        callee = self.add_node(callee_filepath, callee_sig)
        caller.callees.add(callee.id)
        callee.callers.add(caller.id)
        return caller, callee

    def make_root(self, node_id):
        if node_id in self.nodes:
            self.roots.add(node_id)

    def remove_root(self, node_id):
        self.roots.discard(node_id)

    def clear(self):
        self.nodes.clear()
        self.roots.clear()

    def save(self, filepath):
        data = {
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "roots": list(self.roots)
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def load(self, filepath):
        if not os.path.exists(filepath):
            return False
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.clear()
        for k, v in data.get("nodes", {}).items():
            self.nodes[k] = MethodNode.from_dict(v)
        self.roots = set(data.get("roots", []))
        return True