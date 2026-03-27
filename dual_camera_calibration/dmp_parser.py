"""
Windowsダンプ（.dmp）ファイル解析モジュール

TouchDesignerのクラッシュダンプを解析し、
クラッシュ原因の特定に必要な情報を抽出する。
WinDbg等の専用ツールなしで解析が可能。

対応フォーマット:
  - Microsoft Minidump (MDMP)
  - Windows Full/Kernel Crash Dump 32-bit (PAGEDUMP)
  - Windows Full/Kernel Crash Dump 64-bit (PAGEDU64)
"""

import json
import struct
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class MdmpHeader:
    """ミニダンプファイルのヘッダー情報"""

    signature: str
    version: int
    implementation_version: int
    number_of_streams: int
    stream_directory_rva: int
    checksum: int
    timestamp: int
    timestamp_str: str
    flags: int


@dataclass
class MdmpStreamEntry:
    """ストリームディレクトリのエントリ"""

    stream_type: int
    stream_type_name: str
    data_size: int
    rva: int


@dataclass
class SystemInfo:
    """システム情報ストリーム（StreamType 7）"""

    processor_architecture: int
    processor_architecture_name: str
    processor_level: int
    processor_revision: int
    number_of_processors: int
    os_version_major: int
    os_version_minor: int
    os_build_number: int
    os_platform_id: int
    os_version_string: str


@dataclass
class ModuleInfo:
    """ロードされたモジュール（DLL/EXE）の情報"""

    base_address: int
    size: int
    module_name: str
    module_path: str
    timestamp: int
    checksum: int


@dataclass
class ThreadInfo:
    """スレッド情報"""

    thread_id: int
    suspend_count: int
    priority_class: int
    priority: int
    stack_start: int
    stack_size: int


@dataclass
class ExceptionInfo:
    """例外情報ストリーム（StreamType 6）"""

    thread_id: int
    exception_code: int
    exception_code_name: str
    exception_flags: int
    exception_address: int
    number_parameters: int
    exception_parameters: list[int] = field(default_factory=list)


@dataclass
class BugCheckInfo:
    """BugCheck（BSOD）情報（フルクラッシュダンプ用）"""

    bugcheck_code: int
    bugcheck_code_name: str
    bugcheck_parameters: list[int] = field(default_factory=list)


@dataclass
class MinidumpReport:
    """
    ダンプ解析レポート

    解析結果を統合し、クラッシュ情報の保存・表示を行う。
    MDMP（ミニダンプ）とPAGEDUMP/PAGEDU64（フルダンプ）の
    両方の形式に対応。
    """

    file_path: str
    dump_format: str  # "MDMP", "PAGEDUMP", "PAGEDU64"
    header: MdmpHeader
    streams: list[MdmpStreamEntry]
    system_info: SystemInfo | None
    modules: list[ModuleInfo]
    threads: list[ThreadInfo]
    exception: ExceptionInfo | None
    crash_module: str | None
    touchdesigner_modules: list[str]
    bugcheck: BugCheckInfo | None = None
    dump_type_name: str = ""

    def save(self, path: str) -> None:
        """解析レポートをJSONファイルに保存する"""
        data = {
            "file_path": self.file_path,
            "dump_format": self.dump_format,
            "dump_type_name": self.dump_type_name,
            "header": asdict(self.header),
            "streams": [asdict(s) for s in self.streams],
            "system_info": asdict(self.system_info) if self.system_info else None,
            "modules": [
                {
                    **asdict(m),
                    "base_address_hex": f"0x{m.base_address:016X}",
                }
                for m in self.modules
            ],
            "threads": [
                {
                    **asdict(t),
                    "thread_id_hex": f"0x{t.thread_id:08X}",
                }
                for t in self.threads
            ],
            "exception": (
                {
                    **asdict(self.exception),
                    "exception_code_hex": f"0x{self.exception.exception_code:08X}",
                    "exception_address_hex": f"0x{self.exception.exception_address:016X}",
                }
                if self.exception
                else None
            ),
            "bugcheck": (
                {
                    **asdict(self.bugcheck),
                    "bugcheck_code_hex": f"0x{self.bugcheck.bugcheck_code:08X}",
                }
                if self.bugcheck
                else None
            ),
            "crash_module": self.crash_module,
            "touchdesigner_modules": self.touchdesigner_modules,
        }
        Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str) -> "MinidumpReport":
        """JSONファイルから解析レポートを読み込む"""
        data = json.loads(Path(path).read_text())
        header = MdmpHeader(**data["header"])
        streams = [MdmpStreamEntry(**s) for s in data["streams"]]
        system_info = SystemInfo(**data["system_info"]) if data["system_info"] else None

        modules = []
        for m in data["modules"]:
            m.pop("base_address_hex", None)
            modules.append(ModuleInfo(**m))

        threads = []
        for t in data["threads"]:
            t.pop("thread_id_hex", None)
            threads.append(ThreadInfo(**t))

        exception_data = data["exception"]
        if exception_data:
            exception_data.pop("exception_code_hex", None)
            exception_data.pop("exception_address_hex", None)
            exception = ExceptionInfo(**exception_data)
        else:
            exception = None

        bugcheck_data = data.get("bugcheck")
        if bugcheck_data:
            bugcheck_data.pop("bugcheck_code_hex", None)
            bugcheck = BugCheckInfo(**bugcheck_data)
        else:
            bugcheck = None

        return cls(
            file_path=data["file_path"],
            dump_format=data.get("dump_format", "MDMP"),
            header=header,
            streams=streams,
            system_info=system_info,
            modules=modules,
            threads=threads,
            exception=exception,
            crash_module=data["crash_module"],
            touchdesigner_modules=data["touchdesigner_modules"],
            bugcheck=bugcheck,
            dump_type_name=data.get("dump_type_name", ""),
        )

    def summary(self) -> str:
        """クラッシュの概要を人間が読める文字列で返す"""
        lines = []
        lines.append("=" * 60)
        lines.append("ダンプ解析レポート")
        lines.append("=" * 60)
        lines.append(f"ファイル: {self.file_path}")
        lines.append(f"ダンプ形式: {self.dump_format}")
        if self.dump_type_name:
            lines.append(f"ダンプ種別: {self.dump_type_name}")
        lines.append(f"タイムスタンプ: {self.header.timestamp_str}")
        if self.dump_format == "MDMP":
            lines.append(f"ストリーム数: {self.header.number_of_streams}")

        if self.system_info:
            lines.append("")
            lines.append("--- システム情報 ---")
            lines.append(f"OS: {self.system_info.os_version_string}")
            lines.append(f"CPU: {self.system_info.processor_architecture_name}")
            lines.append(f"プロセッサ数: {self.system_info.number_of_processors}")

        if self.bugcheck:
            lines.append("")
            lines.append("--- BugCheck (BSOD) 情報 ---")
            lines.append(
                f"BugCheckコード: 0x{self.bugcheck.bugcheck_code:08X}"
                f" ({self.bugcheck.bugcheck_code_name})"
            )
            for i, param in enumerate(self.bugcheck.bugcheck_parameters):
                lines.append(f"  パラメータ[{i}]: 0x{param:016X}")

        if self.exception:
            lines.append("")
            lines.append("--- 例外情報 ---")
            lines.append(
                f"例外コード: 0x{self.exception.exception_code:08X}"
                f" ({self.exception.exception_code_name})"
            )
            lines.append(
                f"例外アドレス: 0x{self.exception.exception_address:016X}"
            )
            if self.exception.thread_id != 0:
                lines.append(
                    f"スレッドID: 0x{self.exception.thread_id:08X}"
                )
            if self.crash_module:
                lines.append(f"クラッシュモジュール: {self.crash_module}")

        if self.modules:
            lines.append("")
            lines.append(f"--- モジュール ({len(self.modules)}個) ---")
            for m in self.modules:
                lines.append(
                    f"  0x{m.base_address:016X}  {m.size:>10}  {m.module_name}"
                )

        if self.threads:
            lines.append("")
            lines.append(f"--- スレッド ({len(self.threads)}個) ---")
            for t in self.threads:
                lines.append(f"  ID=0x{t.thread_id:08X}  優先度={t.priority}")

        if self.touchdesigner_modules:
            lines.append("")
            lines.append("--- TouchDesigner関連モジュール ---")
            for name in self.touchdesigner_modules:
                lines.append(f"  {name}")

        lines.append("=" * 60)
        return "\n".join(lines)


class MinidumpParser:
    """
    Windowsダンプ（.dmp）ファイルの解析クラス

    TouchDesignerのクラッシュダンプを解析し、
    クラッシュ原因の特定に必要な情報を抽出する。

    対応フォーマット:
      - MDMP: Windows Minidump
      - PAGEDUMP: Windows 32-bit Full/Kernel Crash Dump
      - PAGEDU64: Windows 64-bit Full/Kernel Crash Dump

    使い方:
        1. parse() でダンプファイルを解析（形式は自動判定）
        2. MinidumpReport の summary() でクラッシュ概要を表示
        3. save() でJSON形式のレポートを保存

    Parameters
    ----------
    なし（ステートレス）
    """

    # ストリーム種別の名前マップ（MDMP用）
    STREAM_TYPES: dict[int, str] = {
        0: "UnusedStream",
        1: "ReservedStream0",
        2: "ReservedStream1",
        3: "ThreadListStream",
        4: "ModuleListStream",
        5: "MemoryListStream",
        6: "ExceptionStream",
        7: "SystemInfoStream",
        8: "ThreadExListStream",
        9: "Memory64ListStream",
        10: "CommentStreamA",
        11: "CommentStreamW",
        12: "HandleDataStream",
        13: "FunctionTableStream",
        14: "UnloadedModuleListStream",
        15: "MiscInfoStream",
        16: "MemoryInfoListStream",
        17: "ThreadInfoListStream",
        18: "HandleOperationListStream",
        19: "TokenStream",
        20: "JavaScriptDataStream",
        21: "SystemMemoryInfoStream",
        22: "ProcessVmCountersStream",
        23: "IptTraceStream",
        24: "ThreadNamesStream",
    }

    # 主要な例外コード
    EXCEPTION_CODES: dict[int, str] = {
        0xC0000005: "ACCESS_VIOLATION",
        0xC0000006: "IN_PAGE_ERROR",
        0xC0000008: "INVALID_HANDLE",
        0xC000001D: "ILLEGAL_INSTRUCTION",
        0xC0000025: "NONCONTINUABLE_EXCEPTION",
        0xC0000026: "INVALID_DISPOSITION",
        0xC000008C: "ARRAY_BOUNDS_EXCEEDED",
        0xC000008D: "FLOAT_DENORMAL_OPERAND",
        0xC000008E: "FLOAT_DIVIDE_BY_ZERO",
        0xC000008F: "FLOAT_INEXACT_RESULT",
        0xC0000090: "FLOAT_INVALID_OPERATION",
        0xC0000091: "FLOAT_OVERFLOW",
        0xC0000092: "FLOAT_STACK_CHECK",
        0xC0000093: "FLOAT_UNDERFLOW",
        0xC0000094: "INTEGER_DIVIDE_BY_ZERO",
        0xC0000095: "INTEGER_OVERFLOW",
        0xC0000096: "PRIVILEGED_INSTRUCTION",
        0xC00000FD: "STACK_OVERFLOW",
        0xC0000135: "DLL_NOT_FOUND",
        0xC0000142: "DLL_INIT_FAILED",
        0xC0000409: "STACK_BUFFER_OVERRUN",
        0xC0000417: "INVALID_CRUNTIME_PARAMETER",
        0xC0000602: "UNKNOWN_REVISION",
        0xC06D007E: "MODULE_NOT_FOUND",
        0xC06D007F: "PROCEDURE_NOT_FOUND",
        0xE06D7363: "CPP_EXCEPTION",
        0x40000015: "STATUS_FATAL_APP_EXIT",
        0x40010006: "DBG_PRINTEXCEPTION_C",
        0x4001000A: "DBG_PRINTEXCEPTION_WIDE_C",
        0x80000001: "GUARD_PAGE_VIOLATION",
        0x80000003: "BREAKPOINT",
        0x80000004: "SINGLE_STEP",
    }

    # CPUアーキテクチャの名前マップ
    PROCESSOR_ARCHITECTURES: dict[int, str] = {
        0: "x86",
        5: "ARM",
        6: "IA64",
        9: "x64",
        12: "ARM64",
    }

    # TouchDesigner関連モジュールの判定パターン（小文字で比較）
    TD_MODULE_PATTERNS: list[str] = [
        "touchdesigner",
        "touchengine",
        "touchplayer",
        "derivative",
        "td_",
        "top_",
        "chop_",
        "sop_",
        "dat_",
        "comp_",
        "mat_",
    ]

    # MachineImageType（フルダンプ用）
    MACHINE_IMAGE_TYPES: dict[int, str] = {
        0x014C: "x86",
        0x0200: "IA64",
        0x8664: "x64",
        0xAA64: "ARM64",
        0x01C4: "ARM",
    }

    # DumpType（フルダンプ用）
    DUMP_TYPES: dict[int, str] = {
        0x01: "Full Dump",
        0x02: "Kernel Dump",
        0x03: "Small Dump (Mini)",
        0x05: "Bitmap Dump",
    }

    # 主要なBugCheckコード（BSOD）
    BUGCHECK_CODES: dict[int, str] = {
        0x0000000A: "IRQL_NOT_LESS_OR_EQUAL",
        0x0000001E: "KMODE_EXCEPTION_NOT_HANDLED",
        0x00000019: "BAD_POOL_HEADER",
        0x0000001A: "MEMORY_MANAGEMENT",
        0x00000024: "NTFS_FILE_SYSTEM",
        0x0000002E: "DATA_BUS_ERROR",
        0x0000003B: "SYSTEM_SERVICE_EXCEPTION",
        0x00000050: "PAGE_FAULT_IN_NONPAGED_AREA",
        0x0000007A: "KERNEL_DATA_INPAGE_ERROR",
        0x0000007B: "INACCESSIBLE_BOOT_DEVICE",
        0x0000007E: "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED",
        0x0000007F: "UNEXPECTED_KERNEL_MODE_TRAP",
        0x0000009F: "DRIVER_POWER_STATE_FAILURE",
        0x000000BE: "ATTEMPTED_WRITE_TO_READONLY_MEMORY",
        0x000000C2: "BAD_POOL_CALLER",
        0x000000C4: "DRIVER_VERIFIER_DETECTED_VIOLATION",
        0x000000C5: "DRIVER_CORRUPTED_EXPOOL",
        0x000000D1: "DRIVER_IRQL_NOT_LESS_OR_EQUAL",
        0x000000EF: "CRITICAL_PROCESS_DIED",
        0x000000F4: "CRITICAL_OBJECT_TERMINATION",
        0x00000116: "VIDEO_TDR_TIMEOUT_DETECTED",
        0x00000117: "VIDEO_TDR_FAILURE",
        0x00000119: "VIDEO_SCHEDULER_INTERNAL_ERROR",
        0x00000124: "WHEA_UNCORRECTABLE_ERROR",
        0x00000133: "DPC_WATCHDOG_VIOLATION",
        0x00000139: "KERNEL_SECURITY_CHECK_FAILURE",
        0x0000013A: "KERNEL_MODE_HEAP_CORRUPTION",
        0x00000154: "UNEXPECTED_STORE_EXCEPTION",
        0x000001CA: "SYNTHETIC_WATCHDOG_TIMEOUT",
    }

    # Windows FILETIME エポック変換定数
    _FILETIME_EPOCH_DIFF = 11644473600  # 1601-01-01 → 1970-01-01（秒）

    def parse(self, path: str) -> MinidumpReport:
        """
        ダンプファイルを解析する（形式は自動判定）

        Parameters
        ----------
        path : str
            .dmpファイルのパス

        Returns
        -------
        MinidumpReport
            解析レポート

        Raises
        ------
        ValueError
            ファイルが有効なダンプ形式でない場合
        FileNotFoundError
            ファイルが見つからない場合
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"ファイルが見つかりません: {path}")

        data = file_path.read_bytes()

        if len(data) < 32:
            raise ValueError("ファイルサイズが小さすぎます（最低32バイト必要）")

        # シグネチャ自動判定
        sig4 = data[0:4]
        if sig4 == b"MDMP":
            return self._parse_minidump(data, str(file_path))
        elif sig4 == b"PAGE":
            valid_dump = data[4:8]
            if valid_dump == b"DU64":
                return self._parse_crashdump_64(data, str(file_path))
            elif valid_dump == b"DUMP":
                return self._parse_crashdump_32(data, str(file_path))

        raise ValueError(
            f"未対応のダンプ形式です（シグネチャ: {sig4!r}）。"
            "MDMP, PAGEDUMP, PAGEDU64 のいずれかが必要です。"
        )

    def _parse_minidump(self, data: bytes, file_path: str) -> MinidumpReport:
        """MDMP形式のミニダンプを解析する"""
        header = self._read_header(data)
        streams = self._read_stream_directory(data, header)

        system_info = None
        modules: list[ModuleInfo] = []
        threads: list[ThreadInfo] = []
        exception = None

        for entry in streams:
            if entry.rva + entry.data_size > len(data):
                continue
            if entry.stream_type == 7 and entry.data_size > 0:
                system_info = self._parse_system_info(data, entry)
            elif entry.stream_type == 4 and entry.data_size > 0:
                modules = self._parse_module_list(data, entry)
            elif entry.stream_type == 3 and entry.data_size > 0:
                threads = self._parse_thread_list(data, entry)
            elif entry.stream_type == 6 and entry.data_size > 0:
                exception = self._parse_exception(data, entry)

        crash_module = self._find_crash_module(exception, modules)
        td_modules = self._find_touchdesigner_modules(modules)

        return MinidumpReport(
            file_path=file_path,
            dump_format="MDMP",
            header=header,
            streams=streams,
            system_info=system_info,
            modules=modules,
            threads=threads,
            exception=exception,
            crash_module=crash_module,
            touchdesigner_modules=td_modules,
        )

    def _filetime_to_str(self, filetime: int) -> str:
        """Windows FILETIMEをUTC文字列に変換する"""
        try:
            unix_ts = filetime / 10_000_000 - self._FILETIME_EPOCH_DIFF
            return datetime.fromtimestamp(
                unix_ts, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (OSError, ValueError, OverflowError):
            return f"不明（raw={filetime}）"

    def _parse_crashdump_64(self, data: bytes, file_path: str) -> MinidumpReport:
        """PAGEDU64形式（64-bit）のフルクラッシュダンプを解析する"""
        if len(data) < 0x1030:
            raise ValueError("ファイルサイズが小さすぎます（PAGEDU64ヘッダーに不足）")

        major = struct.unpack_from("<I", data, 0x08)[0]
        minor = struct.unpack_from("<I", data, 0x0C)[0]
        machine_type = struct.unpack_from("<I", data, 0x30)[0]
        num_procs = struct.unpack_from("<I", data, 0x34)[0]
        bugcheck_code = struct.unpack_from("<I", data, 0x38)[0]

        # BugCheckParameter[4] at 0x40 (ULONGLONG * 4)
        bugcheck_params = list(struct.unpack_from("<4Q", data, 0x40))

        # DumpType at 0xF98
        dump_type = struct.unpack_from("<I", data, 0xF98)[0]

        # SystemTime at 0xFA8 (FILETIME)
        system_time = struct.unpack_from("<Q", data, 0xFA8)[0]

        # Comment at 0xFB0 (128 bytes, null-terminated ASCII)
        comment_raw = data[0xFB0:0x1030]
        comment = comment_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

        # _EXCEPTION_RECORD64 at 0xF00
        exception = self._parse_exception_record_64(data, 0xF00)

        arch_name = self.MACHINE_IMAGE_TYPES.get(machine_type, f"Unknown(0x{machine_type:04X})")
        dump_type_name = self.DUMP_TYPES.get(dump_type, f"Unknown({dump_type})")
        bugcheck_name = self.BUGCHECK_CODES.get(
            bugcheck_code, f"UNKNOWN(0x{bugcheck_code:08X})"
        )
        ts_str = self._filetime_to_str(system_time)

        header = MdmpHeader(
            signature="PAGEDU64",
            version=major,
            implementation_version=minor,
            number_of_streams=0,
            stream_directory_rva=0,
            checksum=0,
            timestamp=int(system_time / 10_000_000 - self._FILETIME_EPOCH_DIFF)
            if system_time > 0
            else 0,
            timestamp_str=ts_str,
            flags=0,
        )

        system_info = SystemInfo(
            processor_architecture=machine_type,
            processor_architecture_name=arch_name,
            processor_level=0,
            processor_revision=0,
            number_of_processors=num_procs,
            os_version_major=major,
            os_version_minor=minor,
            os_build_number=0,
            os_platform_id=2,
            os_version_string=f"Windows {major}.{minor}",
        )

        bugcheck = BugCheckInfo(
            bugcheck_code=bugcheck_code,
            bugcheck_code_name=bugcheck_name,
            bugcheck_parameters=bugcheck_params,
        )

        return MinidumpReport(
            file_path=file_path,
            dump_format="PAGEDU64",
            header=header,
            streams=[],
            system_info=system_info,
            modules=[],
            threads=[],
            exception=exception,
            crash_module=None,
            touchdesigner_modules=[],
            bugcheck=bugcheck,
            dump_type_name=dump_type_name,
        )

    def _parse_crashdump_32(self, data: bytes, file_path: str) -> MinidumpReport:
        """PAGEDUMP形式（32-bit）のフルクラッシュダンプを解析する"""
        if len(data) < 0xFC8:
            raise ValueError("ファイルサイズが小さすぎます（PAGEDUMPヘッダーに不足）")

        major = struct.unpack_from("<I", data, 0x08)[0]
        minor = struct.unpack_from("<I", data, 0x0C)[0]
        machine_type = struct.unpack_from("<I", data, 0x20)[0]
        num_procs = struct.unpack_from("<I", data, 0x24)[0]
        bugcheck_code = struct.unpack_from("<I", data, 0x28)[0]

        # BugCheckParameter[4] at 0x2C (ULONG * 4)
        bugcheck_params = list(struct.unpack_from("<4I", data, 0x2C))

        # DumpType at 0xF88
        dump_type = struct.unpack_from("<I", data, 0xF88)[0]

        # SystemTime at 0xFC0 (FILETIME)
        system_time = struct.unpack_from("<Q", data, 0xFC0)[0]

        # Comment at 0x820 (128 bytes, null-terminated ASCII)
        comment_raw = data[0x820:0x8A0]
        comment = comment_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

        # _EXCEPTION_RECORD32 at 0x7D0
        exception = self._parse_exception_record_32(data, 0x7D0)

        arch_name = self.MACHINE_IMAGE_TYPES.get(machine_type, f"Unknown(0x{machine_type:04X})")
        dump_type_name = self.DUMP_TYPES.get(dump_type, f"Unknown({dump_type})")
        bugcheck_name = self.BUGCHECK_CODES.get(
            bugcheck_code, f"UNKNOWN(0x{bugcheck_code:08X})"
        )
        ts_str = self._filetime_to_str(system_time)

        header = MdmpHeader(
            signature="PAGEDUMP",
            version=major,
            implementation_version=minor,
            number_of_streams=0,
            stream_directory_rva=0,
            checksum=0,
            timestamp=int(system_time / 10_000_000 - self._FILETIME_EPOCH_DIFF)
            if system_time > 0
            else 0,
            timestamp_str=ts_str,
            flags=0,
        )

        system_info = SystemInfo(
            processor_architecture=machine_type,
            processor_architecture_name=arch_name,
            processor_level=0,
            processor_revision=0,
            number_of_processors=num_procs,
            os_version_major=major,
            os_version_minor=minor,
            os_build_number=0,
            os_platform_id=2,
            os_version_string=f"Windows {major}.{minor}",
        )

        bugcheck = BugCheckInfo(
            bugcheck_code=bugcheck_code,
            bugcheck_code_name=bugcheck_name,
            bugcheck_parameters=bugcheck_params,
        )

        return MinidumpReport(
            file_path=file_path,
            dump_format="PAGEDUMP",
            header=header,
            streams=[],
            system_info=system_info,
            modules=[],
            threads=[],
            exception=exception,
            crash_module=None,
            touchdesigner_modules=[],
            bugcheck=bugcheck,
            dump_type_name=dump_type_name,
        )

    def _parse_exception_record_64(
        self, data: bytes, offset: int
    ) -> ExceptionInfo | None:
        """_EXCEPTION_RECORD64を解析する（フルダンプ用）"""
        if offset + 0x98 > len(data):
            return None

        exc_code = struct.unpack_from("<I", data, offset)[0]
        exc_flags = struct.unpack_from("<I", data, offset + 4)[0]
        exc_address = struct.unpack_from("<Q", data, offset + 0x10)[0]
        num_params = struct.unpack_from("<I", data, offset + 0x18)[0]

        # 例外コードが0の場合は例外なし
        if exc_code == 0 and exc_address == 0:
            return None

        params = []
        max_params = min(num_params, 15)
        for i in range(max_params):
            p_off = offset + 0x20 + i * 8
            if p_off + 8 > len(data):
                break
            params.append(struct.unpack_from("<Q", data, p_off)[0])

        code_name = self.EXCEPTION_CODES.get(
            exc_code, f"UNKNOWN(0x{exc_code:08X})"
        )

        return ExceptionInfo(
            thread_id=0,
            exception_code=exc_code,
            exception_code_name=code_name,
            exception_flags=exc_flags,
            exception_address=exc_address,
            number_parameters=num_params,
            exception_parameters=params,
        )

    def _parse_exception_record_32(
        self, data: bytes, offset: int
    ) -> ExceptionInfo | None:
        """_EXCEPTION_RECORD32を解析する（32-bitフルダンプ用）"""
        if offset + 0x50 > len(data):
            return None

        exc_code = struct.unpack_from("<I", data, offset)[0]
        exc_flags = struct.unpack_from("<I", data, offset + 4)[0]
        # ExceptionRecord (DWORD, 4)
        exc_address = struct.unpack_from("<I", data, offset + 0x0C)[0]
        num_params = struct.unpack_from("<I", data, offset + 0x10)[0]

        if exc_code == 0 and exc_address == 0:
            return None

        params = []
        max_params = min(num_params, 15)
        for i in range(max_params):
            p_off = offset + 0x14 + i * 4
            if p_off + 4 > len(data):
                break
            params.append(struct.unpack_from("<I", data, p_off)[0])

        code_name = self.EXCEPTION_CODES.get(
            exc_code, f"UNKNOWN(0x{exc_code:08X})"
        )

        return ExceptionInfo(
            thread_id=0,
            exception_code=exc_code,
            exception_code_name=code_name,
            exception_flags=exc_flags,
            exception_address=exc_address,
            number_parameters=num_params,
            exception_parameters=params,
        )

    def _read_header(self, data: bytes) -> MdmpHeader:
        """ヘッダーを解析する（先頭32バイト）"""
        # シグネチャ (4) + バージョン (2) + 実装バージョン (2) +
        # ストリーム数 (4) + ディレクトリRVA (4) + チェックサム (4) +
        # タイムスタンプ (4) + フラグ (8)
        sig = data[0:4]
        if sig != b"MDMP":
            raise ValueError(
                f"無効なミニダンプシグネチャ: {sig!r}（'MDMP'が必要）"
            )

        version, impl_version = struct.unpack_from("<HH", data, 4)
        (
            num_streams,
            stream_dir_rva,
            checksum,
            timestamp,
        ) = struct.unpack_from("<IIII", data, 8)
        (flags,) = struct.unpack_from("<Q", data, 24)

        try:
            ts_str = datetime.fromtimestamp(
                timestamp, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (OSError, ValueError, OverflowError):
            ts_str = f"不明（raw={timestamp}）"

        return MdmpHeader(
            signature="MDMP",
            version=version,
            implementation_version=impl_version,
            number_of_streams=num_streams,
            stream_directory_rva=stream_dir_rva,
            checksum=checksum,
            timestamp=timestamp,
            timestamp_str=ts_str,
            flags=flags,
        )

    def _read_stream_directory(
        self, data: bytes, header: MdmpHeader
    ) -> list[MdmpStreamEntry]:
        """ストリームディレクトリを読み取る"""
        entries = []
        offset = header.stream_directory_rva

        for _ in range(header.number_of_streams):
            if offset + 12 > len(data):
                break
            stream_type, data_size, rva = struct.unpack_from("<III", data, offset)
            entries.append(
                MdmpStreamEntry(
                    stream_type=stream_type,
                    stream_type_name=self.STREAM_TYPES.get(
                        stream_type, f"Unknown({stream_type})"
                    ),
                    data_size=data_size,
                    rva=rva,
                )
            )
            offset += 12

        return entries

    def _parse_system_info(self, data: bytes, entry: MdmpStreamEntry) -> SystemInfo:
        """SystemInfoStream（タイプ7）を解析する"""
        offset = entry.rva
        # MINIDUMP_SYSTEM_INFO:
        #   ProcessorArchitecture (H), ProcessorLevel (H),
        #   ProcessorRevision (H), NumberOfProcessors (B), ProductType (B),
        #   MajorVersion (I), MinorVersion (I), BuildNumber (I),
        #   PlatformId (I), CSDVersionRva (I), ...
        (
            proc_arch,
            proc_level,
            proc_revision,
            num_procs,
            _product_type,
        ) = struct.unpack_from("<HHHBB", data, offset)

        (
            major,
            minor,
            build,
            platform_id,
            _csd_rva,
        ) = struct.unpack_from("<IIIII", data, offset + 8)

        arch_name = self.PROCESSOR_ARCHITECTURES.get(
            proc_arch, f"Unknown({proc_arch})"
        )
        os_str = f"Windows {major}.{minor} Build {build}"

        return SystemInfo(
            processor_architecture=proc_arch,
            processor_architecture_name=arch_name,
            processor_level=proc_level,
            processor_revision=proc_revision,
            number_of_processors=num_procs,
            os_version_major=major,
            os_version_minor=minor,
            os_build_number=build,
            os_platform_id=platform_id,
            os_version_string=os_str,
        )

    def _parse_module_list(
        self, data: bytes, entry: MdmpStreamEntry
    ) -> list[ModuleInfo]:
        """ModuleListStream（タイプ4）を解析する"""
        offset = entry.rva
        (num_modules,) = struct.unpack_from("<I", data, offset)
        offset += 4

        modules = []
        # MINIDUMP_MODULE 構造体のサイズ: 108バイト
        module_entry_size = 108

        for _ in range(num_modules):
            if offset + module_entry_size > len(data):
                break

            # BaseOfImage (Q, 8), SizeOfImage (I, 4), CheckSum (I, 4),
            # TimeDateStamp (I, 4), ModuleNameRva (I, 4)
            base, size, checksum, timestamp, name_rva = struct.unpack_from(
                "<QIIII", data, offset
            )

            # モジュール名の読み取り
            module_path = self._read_minidump_string(data, name_rva)
            # パスからファイル名だけ抽出
            module_name = module_path.rsplit("\\", 1)[-1] if module_path else ""

            modules.append(
                ModuleInfo(
                    base_address=base,
                    size=size,
                    module_name=module_name,
                    module_path=module_path,
                    timestamp=timestamp,
                    checksum=checksum,
                )
            )
            offset += module_entry_size

        return modules

    def _parse_thread_list(
        self, data: bytes, entry: MdmpStreamEntry
    ) -> list[ThreadInfo]:
        """ThreadListStream（タイプ3）を解析する"""
        offset = entry.rva
        (num_threads,) = struct.unpack_from("<I", data, offset)
        offset += 4

        threads = []
        # MINIDUMP_THREAD: ThreadId(I,4) + SuspendCount(I,4) +
        # PriorityClass(I,4) + Priority(I,4) +
        # Teb(Q,8) + Stack(StartOfMemoryRange(Q,8) + DataSize(I,4) + Rva(I,4)) +
        # ThreadContext(DataSize(I,4) + Rva(I,4))
        thread_entry_size = 48

        for _ in range(num_threads):
            if offset + thread_entry_size > len(data):
                break

            thread_id, suspend, pclass, priority = struct.unpack_from(
                "<IIII", data, offset
            )
            _teb = struct.unpack_from("<Q", data, offset + 16)[0]
            stack_start, stack_data_size = struct.unpack_from(
                "<QI", data, offset + 24
            )

            threads.append(
                ThreadInfo(
                    thread_id=thread_id,
                    suspend_count=suspend,
                    priority_class=pclass,
                    priority=priority,
                    stack_start=stack_start,
                    stack_size=stack_data_size,
                )
            )
            offset += thread_entry_size

        return threads

    def _parse_exception(
        self, data: bytes, entry: MdmpStreamEntry
    ) -> ExceptionInfo:
        """ExceptionStream（タイプ6）を解析する"""
        offset = entry.rva

        # ThreadId (I, 4) + __alignment (I, 4)
        (thread_id,) = struct.unpack_from("<I", data, offset)
        offset += 8  # ThreadId + alignment

        # MINIDUMP_EXCEPTION:
        #   ExceptionCode (I, 4) + ExceptionFlags (I, 4) +
        #   ExceptionRecord (Q, 8) + ExceptionAddress (Q, 8) +
        #   NumberParameters (I, 4) + __unusedAlignment (I, 4)
        exc_code, exc_flags = struct.unpack_from("<II", data, offset)
        _exc_record, exc_address = struct.unpack_from("<QQ", data, offset + 8)
        num_params, _unused = struct.unpack_from("<II", data, offset + 24)

        # ExceptionInformation: 最大15個のUINT64
        params = []
        param_offset = offset + 32
        max_params = min(num_params, 15)
        for i in range(max_params):
            if param_offset + 8 > len(data):
                break
            (val,) = struct.unpack_from("<Q", data, param_offset)
            params.append(val)
            param_offset += 8

        code_name = self.EXCEPTION_CODES.get(
            exc_code, f"UNKNOWN(0x{exc_code:08X})"
        )

        return ExceptionInfo(
            thread_id=thread_id,
            exception_code=exc_code,
            exception_code_name=code_name,
            exception_flags=exc_flags,
            exception_address=exc_address,
            number_parameters=num_params,
            exception_parameters=params,
        )

    def _read_minidump_string(self, data: bytes, rva: int) -> str:
        """
        MINIDUMP_STRING構造体を読み取る

        構造: Length (I, 4バイト) + Buffer (UTF-16LEバイト列)
        """
        if rva + 4 > len(data):
            return ""

        (length,) = struct.unpack_from("<I", data, rva)
        str_start = rva + 4
        str_end = str_start + length

        if str_end > len(data):
            return ""

        try:
            return data[str_start:str_end].decode("utf-16-le")
        except UnicodeDecodeError:
            return ""

    def _find_crash_module(
        self,
        exception: ExceptionInfo | None,
        modules: list[ModuleInfo],
    ) -> str | None:
        """例外アドレスからクラッシュモジュールを特定する"""
        if exception is None:
            return None

        addr = exception.exception_address
        for mod in modules:
            if mod.base_address <= addr < mod.base_address + mod.size:
                return mod.module_name

        return None

    def _find_touchdesigner_modules(
        self, modules: list[ModuleInfo]
    ) -> list[str]:
        """TouchDesigner関連モジュールをフィルタリングする"""
        td_modules = []
        for mod in modules:
            name_lower = mod.module_name.lower()
            path_lower = mod.module_path.lower()
            for pattern in self.TD_MODULE_PATTERNS:
                if pattern in name_lower or pattern in path_lower:
                    td_modules.append(mod.module_name)
                    break
        return td_modules
