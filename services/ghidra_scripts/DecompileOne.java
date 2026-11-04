// Decompiliert EINE Funktion zu C (Headless). Args: <file_vaddr_hex> <out_file>
// Ersetzt DecompileOne.py (Jython ist in Ghidra 12.x entfernt) -> native Java-GhidraScript.
// @category LipyRE
import java.io.FileWriter;

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.util.task.ConsoleTaskMonitor;

public class DecompileOne extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            println("DecompileOne: brauche <vaddr_hex> <out_file>");
            return;
        }
        String vaddrHex = args[0];
        String outFile = args[1];

        String hex = vaddrHex;
        if (hex.startsWith("0x") || hex.startsWith("0X")) {
            hex = hex.substring(2);
        }
        long vaddr = Long.parseLong(hex, 16);

        // Datei-vaddr (Base 0) -> Ghidra-Adresse (ImageBase + vaddr)
        long base = currentProgram.getImageBase().getOffset();
        AddressSpace space = currentProgram.getAddressFactory().getDefaultAddressSpace();
        Address target = space.getAddress(base + vaddr);

        FunctionManager fm = currentProgram.getFunctionManager();
        ConsoleTaskMonitor mon = new ConsoleTaskMonitor();
        Function func = fm.getFunctionContaining(target);
        if (func == null) {
            new CreateFunctionCmd(target).applyTo(currentProgram, mon);
            func = fm.getFunctionContaining(target);
        }

        StringBuilder sb = new StringBuilder();
        if (func == null) {
            sb.append("// Keine Funktion an ").append(vaddrHex)
              .append(" (auch nach CreateFunctionCmd).");
        } else {
            DecompInterface di = new DecompInterface();
            try {
                di.openProgram(currentProgram);
                DecompileResults res = di.decompileFunction(func, 120, mon);
                if (res != null && res.decompileCompleted()) {
                    sb.append("// ").append(func.getName())
                      .append(String.format("  (Ghidra-Decompile, ImageBase 0x%x)", base))
                      .append("\n");
                    sb.append(res.getDecompiledFunction().getC());
                } else {
                    String em = (res != null) ? res.getErrorMessage() : "unbekannt";
                    sb.append("// Decompile fehlgeschlagen: ").append(em);
                }
            } finally {
                di.dispose();
            }
        }

        FileWriter fw = new FileWriter(outFile);
        try {
            fw.write(sb.toString());
        } finally {
            fw.close();
        }
        println("DecompileOne: geschrieben -> " + outFile);
    }
}
