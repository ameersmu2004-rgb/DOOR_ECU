# uds_helpers.py

def hex2(x):
    return f"0x{x:02X}"

def hex4(x):
    return f"0x{x:04X}"

def uds_sid_name(sid: int) -> str:
    names = {
        0x10: "DiagnosticSessionControl",
        0x11: "ECUReset",
        0x22: "ReadDataByIdentifier",
        0x23: "ReadMemoryByAddress",
        0x27: "SecurityAccess",
        0x2E: "WriteDataByIdentifier",
        0x3D: "WriteMemoryByAddress",
        0x3E: "TesterPresent",
        0x7F: "NegativeResponse",
    }
    return names.get(sid, f"UnknownSID({hex2(sid)})")

def nrc_name(nrc: int) -> str:
    names = {
        0x10: "GeneralReject",
        0x11: "ServiceNotSupported",
        0x12: "SubFunctionNotSupported",
        0x13: "IncorrectMessageLengthOrInvalidFormat",
        0x22: "ConditionsNotCorrect",
        0x31: "RequestOutOfRange",
        0x33: "SecurityAccessDenied",
        0x35: "InvalidKey",
        0x36: "ExceededNumberOfAttempts",
        0x37: "RequiredTimeDelayNotExpired",
        0x78: "ResponsePending",
        0x7E: "ServiceNotSupportedInActiveSession",
    }
    return names.get(nrc, f"UnknownNRC({hex2(nrc)})")
