import os
from core.infrastructure.command_runner import CommandRunner
from services.callgraph_service import is_system_api
from core.application.event_bus import EventBus


class GraphvizExportService:
    @staticmethod
    def _get_dot_path():
        """Sucht die dot.exe direkt im portablen tools-Ordner (umgeht Windows-PATH Probleme)."""
        # Navigiert von services/graphviz_service.py -> ../tools/graphviz
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        gv_dir = os.path.join(base_dir, "tools", "graphviz")

        if os.path.exists(gv_dir):
            for root, _, files in os.walk(gv_dir):
                if "dot.exe" in files:
                    # Absoluten Pfad in Anführungszeichen für den CommandRunner
                    return f'"{os.path.join(root, "dot.exe")}"'

        return "dot"  # Fallback auf System-PATH (für Mac/Linux oder manuelle Installation)

    @staticmethod
    def export_to_svg(cg_manager, output_dir: str) -> str:
        if not cg_manager.nodes:
            return None

        dot_file = os.path.join(output_dir, "callgraph.dot")
        svg_file = os.path.join(output_dir, "callgraph.svg")

        # .dot Syntax aufbauen
        lines = []
        lines.append('digraph CallGraph {')
        lines.append('    node [shape=box, style=filled, fontname="Consolas", fontsize=10];')
        lines.append('    edge [fontname="Consolas", fontsize=8, color="#666666"];')
        lines.append('    rankdir=TB;')

        # Nodes definieren
        for node_id, node in cg_manager.nodes.items():
            clean_sig = node.signature.split('(')[0]
            filename = os.path.basename(node.filepath)
            label = f"{clean_sig}\\n{filename}"

            color = "#e0e0e0" if is_system_api("L" + node.filepath) else "#add8e6"
            lines.append(f'    "{node_id}" [label="{label}", fillcolor="{color}"];')

        # Edges definieren
        for node_id, node in cg_manager.nodes.items():
            for callee_id in node.callees:
                lines.append(f'    "{node_id}" -> "{callee_id}";')

        lines.append('}')

        # Datei schreiben
        os.makedirs(output_dir, exist_ok=True)
        with open(dot_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        # Exakten Pfad zur Exe ermitteln
        dot_exe = GraphvizExportService._get_dot_path()
        cmd = f'{dot_exe} -Tsvg "{dot_file}" -o "{svg_file}"'

        res = CommandRunner.run_blocking(cmd, cwd=output_dir)

        if res.returncode == 0 and os.path.exists(svg_file):
            return svg_file
        else:
            EventBus.publish("LOG_INFO", f"[!] Graphviz Error. Kommando: {cmd} | Fehler: {res.stderr}")
            return dot_file