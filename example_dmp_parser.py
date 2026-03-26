"""
TouchDesignerクラッシュダンプ（.dmp）解析の使用例

このスクリプトは以下の流れを示します:
  1. .dmpファイルの解析
  2. クラッシュ概要の表示
  3. 解析レポートのJSON保存
  4. 合成テストデータによるデモ
"""

import struct
import tempfile
from pathlib import Path

from dual_camera_calibration import MinidumpParser, MinidumpReport


def main():
    # ============================================================
    # 実際の.dmpファイルがある場合
    # ============================================================
    # parser = MinidumpParser()
    # report = parser.parse("path/to/TouchDesigner.dmp")
    # print(report.summary())
    # report.save("crash_report.json")

    # ============================================================
    # デモ: 合成テストデータで解析フローを実演
    # ============================================================
    print("=== 合成テストデータによるデモ ===\n")
    demo_with_synthetic_data()


def _build_minidump_string(text: str) -> bytes:
    """MINIDUMP_STRING構造体を構築する（テスト用）"""
    encoded = text.encode("utf-16-le")
    return struct.pack("<I", len(encoded)) + encoded


def _build_synthetic_minidump() -> bytes:
    """解析テスト用の合成ミニダンプバイナリを構築する"""
    # --- ストリームデータを先に構築 ---

    # SystemInfoStream (タイプ7)
    # ProcessorArchitecture(H) + ProcessorLevel(H) + ProcessorRevision(H) +
    # NumberOfProcessors(B) + ProductType(B) +
    # MajorVersion(I) + MinorVersion(I) + BuildNumber(I) +
    # PlatformId(I) + CSDVersionRva(I) + ...
    system_info = struct.pack(
        "<HHHBB",
        9,     # x64
        6,     # ProcessorLevel
        0x3A09,  # ProcessorRevision
        8,     # 8 processors
        1,     # VER_NT_WORKSTATION
    )
    system_info += struct.pack(
        "<IIIII",
        10,    # Windows 10
        0,     # Minor version
        19045, # Build 19045
        2,     # VER_PLATFORM_WIN32_NT
        0,     # CSDVersionRva（未使用）
    )
    # 残りのフィールドをゼロ埋め（SuiteMask, Reserved2, CPU info等）
    system_info += b"\x00" * 24

    # モジュール名の文字列データ
    module_strings = [
        "C:\\Program Files\\Derivative\\TouchDesigner\\bin\\TouchDesigner.exe",
        "C:\\Windows\\System32\\ntdll.dll",
        "C:\\Windows\\System32\\kernel32.dll",
        "C:\\Program Files\\Derivative\\TouchDesigner\\bin\\TouchEngine.dll",
    ]

    # モジュールリストの構築
    # まず文字列領域のRVAを計算するため、仮のオフセットを使う
    # ヘッダー(32) + ストリームディレクトリ(12*4=48) = 80 から開始
    header_size = 32
    num_streams = 4
    stream_dir_size = 12 * num_streams
    data_start = header_size + stream_dir_size  # 80

    # ストリームデータの配置を計算
    # SystemInfo
    system_info_rva = data_start
    system_info_size = len(system_info)

    # ThreadList
    thread_list_rva = system_info_rva + system_info_size
    num_threads = 3
    # NumberOfThreads(4) + threads(48 * num_threads)
    thread_data = struct.pack("<I", num_threads)
    for i in range(num_threads):
        thread_id = 0x1000 + i * 4
        # ThreadId(I) + SuspendCount(I) + PriorityClass(I) + Priority(I) +
        # Teb(Q) + Stack.StartOfMemoryRange(Q) + Stack.Memory.DataSize(I) +
        # Stack.Memory.Rva(I) + ThreadContext.DataSize(I) + ThreadContext.Rva(I)
        thread_data += struct.pack(
            "<IIII Q QII II",
            thread_id, 0, 0x20, 8 + i,  # ID, Suspend, PriorityClass, Priority
            0x7FF000000000 + i * 0x1000,  # Teb
            0x00000050F0000000 + i * 0x10000, 0x4000, 0,  # Stack
            0, 0,  # ThreadContext
        )
    thread_list_size = len(thread_data)

    # 文字列領域
    strings_rva = thread_list_rva + thread_list_size
    string_entries = []
    current_rva = strings_rva
    for s in module_strings:
        string_bytes = _build_minidump_string(s)
        string_entries.append((current_rva, string_bytes))
        current_rva += len(string_bytes)
    total_strings_size = current_rva - strings_rva

    # ModuleList
    module_list_rva = strings_rva + total_strings_size
    module_bases = [
        0x00007FF600000000,  # TouchDesigner.exe
        0x00007FFB80000000,  # ntdll.dll
        0x00007FFB70000000,  # kernel32.dll
        0x00007FF600100000,  # TouchEngine.dll
    ]
    module_sizes = [0x200000, 0x1C0000, 0x100000, 0x80000]

    module_data = struct.pack("<I", len(module_strings))
    for i, (base, size) in enumerate(zip(module_bases, module_sizes)):
        name_rva = string_entries[i][0]
        # BaseOfImage(Q,8) + SizeOfImage(I,4) + CheckSum(I,4) +
        # TimeDateStamp(I,4) + ModuleNameRva(I,4) +
        # VersionInfo(52) + CvRecord(8) + MiscRecord(8) +
        # Reserved0(Q,8) + Reserved1(Q,8)
        # Total = 8+4+4+4+4+52+8+8+8+8 = 108
        entry = struct.pack("<QIIII", base, size, 0, 1700000000, name_rva)
        entry += b"\x00" * 52  # VS_FIXEDFILEINFO
        entry += b"\x00" * 8   # CvRecord
        entry += b"\x00" * 8   # MiscRecord
        entry += b"\x00" * 8   # Reserved0
        entry += b"\x00" * 8   # Reserved1
        module_data += entry
    module_list_size = len(module_data)

    # ExceptionStream
    exception_rva = module_list_rva + module_list_size
    # クラッシュ: TouchDesigner.exe内でACCESS_VIOLATION
    crash_address = 0x00007FF600001234
    exception_data = struct.pack("<I", 0x1000)  # ThreadId
    exception_data += struct.pack("<I", 0)  # alignment
    # MINIDUMP_EXCEPTION:
    exception_data += struct.pack(
        "<II QQ II",
        0xC0000005,  # ACCESS_VIOLATION
        0,           # ExceptionFlags
        0,           # ExceptionRecord
        crash_address,  # ExceptionAddress
        2,           # NumberParameters
        0,           # unusedAlignment
    )
    # ExceptionInformation (2 params: read/write flag + address)
    exception_data += struct.pack("<Q", 0)   # 0 = read violation
    exception_data += struct.pack("<Q", 0x0000000000000000)  # null pointer
    # 残りのパラメータスロット（13個）をゼロ埋め
    exception_data += b"\x00" * (13 * 8)
    # ThreadContext location descriptor
    exception_data += struct.pack("<II", 0, 0)
    exception_size = len(exception_data)

    # --- ストリームディレクトリの構築 ---
    stream_dir = b""
    stream_dir += struct.pack("<III", 7, system_info_size, system_info_rva)
    stream_dir += struct.pack("<III", 3, thread_list_size, thread_list_rva)
    stream_dir += struct.pack("<III", 4, module_list_size, module_list_rva)
    stream_dir += struct.pack("<III", 6, exception_size, exception_rva)

    # --- ヘッダーの構築 ---
    header = struct.pack(
        "<4s HH IIII Q",
        b"MDMP",
        0xA793,     # version
        0x0006,     # implementation version
        num_streams,
        header_size,  # StreamDirectoryRva (直後)
        0,          # CheckSum
        1700000000, # TimeDateStamp (2023-11-14頃)
        0x00000002, # MiniDumpWithFullMemory flag
    )

    # --- 全体を結合 ---
    result = header + stream_dir + system_info
    result += thread_data
    for _, string_bytes in string_entries:
        result += string_bytes
    result += module_data
    result += exception_data

    return result


def demo_with_synthetic_data():
    """合成テストデータで解析デモを実行する"""
    # 合成ミニダンプを一時ファイルに書き出し
    dmp_data = _build_synthetic_minidump()

    with tempfile.NamedTemporaryFile(suffix=".dmp", delete=False) as f:
        f.write(dmp_data)
        dmp_path = f.name

    try:
        parser = MinidumpParser()
        report = parser.parse(dmp_path)

        # クラッシュ概要を表示
        print(report.summary())

        # JSONレポートとして保存
        report_path = "crash_report_demo.json"
        report.save(report_path)
        print(f"\nレポートを {report_path} に保存しました。")

        # 個別情報へのアクセス例
        print("\n--- 個別フィールドへのアクセス例 ---")
        if report.exception:
            print(f"例外コード: 0x{report.exception.exception_code:08X}")
            print(f"例外名: {report.exception.exception_code_name}")
        if report.crash_module:
            print(f"クラッシュモジュール: {report.crash_module}")

        # TouchDesigner関連モジュール一覧
        if report.touchdesigner_modules:
            print("\nTouchDesigner関連モジュール:")
            for mod in report.touchdesigner_modules:
                print(f"  {mod}")

        # 保存したレポートの再読み込みテスト
        loaded = MinidumpReport.load(report_path)
        print(f"\nJSON保存/読み込みテスト: OK")
        print(f"  再読み込み後のクラッシュモジュール: {loaded.crash_module}")

    finally:
        Path(dmp_path).unlink(missing_ok=True)
        Path("crash_report_demo.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
