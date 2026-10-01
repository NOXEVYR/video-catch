"""Generate PyInstaller version resources for both Windows executables."""
from version import VERSION, FILE_VERSION, PRODUCT_NAME


def resource(filename):
    strings = {"CompanyName": "NOXEVYR", "FileDescription": PRODUCT_NAME if filename == "VideoCatch.exe" else PRODUCT_NAME + " AI Client",
               "FileVersion": VERSION, "ProductVersion": VERSION, "ProductName": PRODUCT_NAME,
               "OriginalFilename": filename, "InternalName": filename.removesuffix(".exe")}
    entries = ",\n".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    return f"""VSVersionInfo(ffi=FixedFileInfo(filevers={FILE_VERSION!r}, prodvers={FILE_VERSION!r},
mask=0x3f, flags=2, OS=0x40004, fileType=1, subtype=0, date=(0,0)),
kids=[StringFileInfo([StringTable('040904B0', [{entries}])]),
VarFileInfo([VarStruct('Translation', [1033,1200])])])
"""
