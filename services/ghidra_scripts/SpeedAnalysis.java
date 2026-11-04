// Schaltet die langsamsten Ghidra-Analyzer ab -> deutlich schnellerer Erstlauf.
// Ersetzt SpeedAnalysis.py (Jython ist in Ghidra 12.x entfernt) -> native Java-GhidraScript.
// Als -preScript VOR der Auto-Analyse laufen lassen.
// @category LipyRE
import ghidra.app.script.GhidraScript;
import ghidra.framework.options.Options;
import ghidra.program.model.listing.Program;

public class SpeedAnalysis extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] slow = {
            "Decompiler Parameter ID",
            "Decompiler Switch Analysis",
            "Aggressive Instruction Finder",
            "Call Convention ID",
            "Stack",
        };
        Options opts = currentProgram.getOptions(Program.ANALYSIS_PROPERTIES);
        for (String k : slow) {
            try {
                if (opts.contains(k)) {
                    opts.setBoolean(k, false);
                } else {
                    println("SpeedAnalysis: Option nicht vorhanden: '" + k + "'");
                }
            } catch (Exception e) {
                println("SpeedAnalysis: Option '" + k + "' nicht gesetzt: " + e);
            }
        }
        println("SpeedAnalysis: langsame Analyzer deaktiviert.");
    }
}
