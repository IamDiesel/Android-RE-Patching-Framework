# Decompiliert EINE Funktion zu C. Aufruf durch analyzeHeadless -postScript.
# Argumente: <file_vaddr_hex> <out_file>
# @category LipyRE
# @runtime Jython
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
from ghidra.app.cmd.function import CreateFunctionCmd

args = getScriptArgs()
vaddr_hex = args[0]
out_file = args[1]

# Datei-vaddr (Base 0) -> Ghidra-Adresse (ImageBase + vaddr)
base = currentProgram.getImageBase().getOffset()
space = currentProgram.getAddressFactory().getDefaultAddressSpace()
target = space.getAddress(base + int(vaddr_hex, 16))

fm = currentProgram.getFunctionManager()
monitor = ConsoleTaskMonitor()
func = fm.getFunctionContaining(target)
if func is None:
    CreateFunctionCmd(target).applyTo(currentProgram, monitor)
    func = fm.getFunctionContaining(target)

lines = []
if func is None:
    lines.append("// Keine Funktion an %s (auch nach CreateFunctionCmd)." % vaddr_hex)
else:
    di = DecompInterface()
    di.openProgram(currentProgram)
    res = di.decompileFunction(func, 120, monitor)
    if res is not None and res.decompileCompleted():
        lines.append("// %s  (Ghidra-Decompile, ImageBase 0x%x)" % (func.getName(), base))
        lines.append(res.getDecompiledFunction().getC())
    else:
        em = res.getErrorMessage() if res is not None else "unbekannt"
        lines.append("// Decompile fehlgeschlagen: %s" % em)

fh = open(out_file, "w")
fh.write("\n".join(lines))
fh.close()
