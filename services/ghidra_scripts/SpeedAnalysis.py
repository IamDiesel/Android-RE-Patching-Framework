# Schaltet die langsamsten Ghidra-Analyzer ab -> deutlich schnellerer Erstlauf.
# Trade-off: etwas weniger Decompiler-Parameter-Praezision, dafuer viel schneller.
# @category LipyRE
# @runtime Jython
_slow = [
    "Decompiler Parameter ID",
    "Decompiler Switch Analysis",
    "Aggressive Instruction Finder",
    "Call Convention ID",
    "Stack",
]
for _k in _slow:
    try:
        setAnalysisOption(currentProgram, _k, "false")
    except Exception as _e:
        print("SpeedAnalysis: Option '%s' nicht gesetzt: %s" % (_k, _e))
print("SpeedAnalysis: langsame Analyzer deaktiviert.")
