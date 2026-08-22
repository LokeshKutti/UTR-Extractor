# PyInstaller spec for the standalone desktop build.
#
#   pyinstaller utrextractor.spec --noconfirm
#
# --onedir (not --onefile): a folder of loose files starts faster (no
# unzip-to-temp on every launch) and is far more forgiving for the binary-
# heavy packages this project depends on -- onnxruntime, opencv and PyMuPDF
# all ship native DLLs and data files that onefile's single-archive model
# makes easy to get wrong.
#
# collect_all is used, not just hiddenimports, for every package here that
# ships its own binary/data files alongside its Python code: PyInstaller's
# static import scanner sees the .py files, but it does not know to copy a
# model file or a .dll sitting next to them unless told to.

from PyInstaller.utils.hooks import collect_all

datas = [("web", "web"), (".env.example", ".")]
binaries = []
hiddenimports = []

# rapidocr_onnxruntime bundles its .onnx model weights inside the package
# itself; onnxruntime, cv2 and pymupdf all ship native DLLs. google.genai is
# imported lazily (only when the optional AI button is clicked), so
# PyInstaller's static scan can miss it entirely without this.
for pkg in ["rapidocr_onnxruntime", "onnxruntime", "cv2", "fitz", "google.genai"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["desktop_launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="UTR Extractor",
    console=True,          # kept visible on purpose -- see README packaging notes
    icon=None,
)
COLLECT(
    exe, a.binaries, a.datas,
    name="UTR Extractor",
)
