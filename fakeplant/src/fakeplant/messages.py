"""Free-text messages to the Okan IOC (P2.8, #1254): what reaches the control room with no system behind it. Mail to
the IOC mailbox, radio calls the CRO typed in, posts in the operations Teams channel and a field operator's note from
the phone, in the words people use. Some name one asset by its tag or a name people use for it, some could be about
any of several, some ask for nothing.

`MESSAGES` is the export (`messages.csv`); `ANSWERS` is what each one is: a case about the entity named by its key, a
question for the data steward (the text does not say which entity), or nothing to do. Fuzer's reader is scored on it
(tests/ops/test_messages.py, docs/eval-results.md). Written by hand, so the same every time; the dates sit in the last
days before the demo's today (AS_OF) and roll forward with it.
"""

# (message_id, received_at, channel, sender, subject, text)
MESSAGES = [
    ("MSG-0001", "2025-12-29T06:52:00", "mailbox", "Kemi Adebayo, Rotating Equipment Engineer",
     "Train B 2nd stage vibration", "Morning IOC, 20-K-2200B vibration has crept up since the night shift, about 6 mm/s on "
     "the local gauge now. Can someone have it looked at before it trips again?"),
    ("MSG-0002", "2025-12-29T07:15:00", "radio", "PP CRO", "",
     "Field op reports oil export pump 10-P-1001B seal weeping at the drive end, small drips into the bund."),
    ("MSG-0003", "2025-12-29T08:03:00", "teams", "GGCP Operations", "",
     "Inlet separator gauge glass reads lower than the transmitter again. Think the level transmitter is drifting."),
    ("MSG-0004", "2025-12-29T08:40:00", "phone", "Femi Okoro, Field Operator", "",
     "OK-J07-03 wellhead weeping at the wing valve flange, slight gas smell on the cellar deck."),
    ("MSG-0005", "2025-12-29T09:12:00", "mailbox", "Tunde Bello, Production Engineer", "Flare meter",
     "The flare meter has read zero since 05:00. Please get the 25-FT-4003 transmitter checked, we need it for the "
     "allocation."),
    ("MSG-0006", "2025-12-29T10:31:00", "radio", "GGCP CRO", "",
     "K-2300A tripped on high discharge temp, restarted at 10:20, running."),
    ("MSG-0007", "2025-12-29T11:05:00", "radio", "ST1 CRO", "",
     "PT-2001 reading erratic, jumping 5 bar either way."),
    ("MSG-0008", "2025-12-29T12:20:00", "teams", "PP Operations", "",
     "Test separator dump valve sticking again, had to stroke it by hand twice this morning."),
    ("MSG-0009", "2025-12-29T13:02:00", "mailbox", "Marine Coordinator", "J12 crane",
     "Crane on J12 out of service until the boat brings the part, no lifts there before Thursday."),
    ("MSG-0010", "2025-12-29T13:10:00", "mailbox", "Kemi Adebayo, Rotating Equipment Engineer", "RE: Train B 2nd stage vibration",
     "Thanks, received."),
    ("MSG-0011", "2025-12-29T14:45:00", "teams", "GGCP Operations", "",
     "Lift gas header pressure dropping on GGCP, down to 1150 psig from 1276."),
    ("MSG-0012", "2025-12-29T15:30:00", "mailbox", "Tunde Bello, Production Engineer", "Well test",
     "Please put OK-NWP-05 on test this week, its last test was 40 days ago."),
    ("MSG-0013", "2025-12-29T16:12:00", "radio", "PP CRO", "",
     "Swapped 10-P-1001A to 10-P-1001C for the seal job, A is isolated and drained."),
    ("MSG-0014", "2025-12-30T06:20:00", "phone", "Femi Okoro, Field Operator", "",
     "20-P-9999 making a grinding noise, smells hot."),
    ("MSG-0015", "2025-12-30T07:05:00", "phone", "Ada Nwosu, Field Operator", "",
     "Glycol contactor 20-V-2400 high level alarm standing since 14:00 yesterday, gauge glass agrees."),
    ("MSG-0016", "2025-12-30T07:48:00", "teams", "GGCP Operations", "",
     "Anyone know why train C tripped last night?"),
    ("MSG-0017", "2025-12-30T08:30:00", "mailbox", "Field Service, compressor vendor", "Service bulletin",
     "Service bulletin attached for the Taurus 60 fuel nozzles. It applies to all three gas turbine drivers."),
    ("MSG-0018", "2025-12-30T09:14:00", "radio", "GGCP CRO", "",
     "Compression bypass opened during the train A restart, closed again at 09:10."),
    ("MSG-0019", "2025-12-30T09:20:00", "teams", "PP Operations", "", "Copy that, thanks"),
    ("MSG-0020", "2025-12-30T10:02:00", "phone", "Ada Nwosu, Field Operator", "",
     "V-2002 level glass cracked, isolated it."),
    ("MSG-0021", "2025-12-30T11:40:00", "mailbox", "Ngozi Eze", "Automatic reply: Out of office",
     "I am out of the office until 5 January with no access to email. For urgent matters call the OIM."),
    ("MSG-0022", "2025-12-30T12:15:00", "phone", "Femi Okoro, Field Operator", "",
     "Pump B on oil export is noisy, sounds like cavitation."),
    ("MSG-0023", "2025-12-30T13:05:00", "radio", "ST1 CRO", "",
     "Lift gas header low on ST1 side, GGCP says 20-PT-3003 is fine their end."),
]

# message_id -> ("case", the entity's key as a ref) | ("steward",) | ("handled",)
ANSWERS = {
    "MSG-0001": ("case", "tag:20-K-2200B"),
    "MSG-0002": ("case", "tag:10-P-1001B"),
    "MSG-0003": ("case", "tag:20-V-2001"),
    "MSG-0004": ("case", "well:OK-J07-03"),
    "MSG-0005": ("case", "tag:25-FT-4003"),
    "MSG-0006": ("case", "tag:20-K-2300A"),
    "MSG-0007": ("steward",),  # PT-2001 is on a separator in five areas; the text doesn't say which
    "MSG-0008": ("steward",),  # every platform has a test separator
    "MSG-0009": ("steward",),  # a crane is not in the register; J12 is only the place
    "MSG-0010": ("handled",),
    "MSG-0011": ("case", "tag:20-PT-3003"),
    "MSG-0012": ("case", "well:OK-NWP-05"),
    "MSG-0013": ("case", "tag:10-P-1001A"),
    "MSG-0014": ("steward",),  # no 20-P-9999 in the register
    "MSG-0015": ("case", "tag:20-V-2400"),
    "MSG-0016": ("steward",),  # a train is not one entity
    "MSG-0017": ("steward",),  # names no tag or alias: a person says what it is
    "MSG-0018": ("case", "tag:20-XV-3100"),
    "MSG-0019": ("handled",),
    "MSG-0020": ("steward",),  # V-2002 is in five areas
    "MSG-0021": ("handled",),
    "MSG-0022": ("steward",),  # "pump B on oil export" names no tag
    "MSG-0023": ("case", "tag:30-PT-3003"),  # ST1's header; "lift gas header" is the pack's name for GGCP's
}

COLUMNS = ("message_id", "received_at", "channel", "sender", "subject", "text")


def rows() -> list[dict]:
    """The export's rows, oldest first."""
    return [dict(zip(COLUMNS, m, strict=True)) for m in MESSAGES]
