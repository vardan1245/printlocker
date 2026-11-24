import io

import pytest

from app.domain.codes import hash_code, new_pin, new_qr_token, parse_qr, qr_payload
from app.domain.pdf import InvalidPdf, count_pdf_pages
from app.domain.reservation import (
    CompartmentInfo,
    NoCompartmentAvailable,
    choose_compartments,
    free_count,
)
from app.domain.sheets import (
    InvalidPrintOptions,
    compartments_needed,
    parse_page_range,
    plan_job,
    sheets_needed,
    split_sheets,
)
from app.domain.states import (
    ALLOWED,
    CompartmentState as CS,
    InvalidTransition,
    JobStatus as S,
    TERMINAL,
    check_transition,
    compartment_state_after,
)
from app.domain.storage import EncryptedFileStore


# ---------- state machine ----------

HAPPY_PATH = [S.UPLOADED, S.QUEUED, S.PRINTING, S.DELIVERING, S.READY, S.COLLECTED]


def test_happy_path_is_allowed():
    for a, b in zip(HAPPY_PATH, HAPPY_PATH[1:]):
        check_transition(a, b)


def test_terminal_states_have_no_exits():
    assert ALLOWED[S.EXPIRED] == frozenset({S.COLLECTED})  # late offline pickup report
    for s in TERMINAL - {S.EXPIRED}:
        assert ALLOWED[s] == frozenset()
        for t in S:
            with pytest.raises(InvalidTransition):
                check_transition(s, t)


@pytest.mark.parametrize(
    "a,b",
    [
        (S.UPLOADED, S.READY),
        (S.QUEUED, S.READY),
        (S.PRINTING, S.CANCELLED),  # paper already moving, user can't cancel
        (S.READY, S.FAILED),
        (S.READY, S.QUEUED),
        (S.DELIVERING, S.EXPIRED),
    ],
)
def test_illegal_transitions(a, b):
    with pytest.raises(InvalidTransition):
        check_transition(a, b)


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (S.UPLOADED, S.QUEUED, CS.RESERVED),
        (S.QUEUED, S.PRINTING, None),
        (S.PRINTING, S.DELIVERING, None),
        (S.DELIVERING, S.READY, CS.OCCUPIED),
        (S.READY, S.COLLECTED, CS.FREE),
        (S.QUEUED, S.CANCELLED, CS.FREE),
        (S.PRINTING, S.FAILED, CS.FREE),
        (S.DELIVERING, S.FAILED, CS.FAULT),
        (S.READY, S.EXPIRED, CS.EXPIRED),
        (S.EXPIRED, S.COLLECTED, CS.FREE),
    ],
)
def test_compartment_follows_job(a, b, expected):
    assert compartment_state_after(a, b) is expected


# ---------- page ranges and sheets ----------

@pytest.mark.parametrize(
    "spec,total,expected",
    [
        (None, 3, [1, 2, 3]),
        ("", 2, [1, 2]),
        ("1-3,5", 6, [1, 2, 3, 5]),
        ("8-", 10, [8, 9, 10]),
        ("-2", 10, [1, 2]),
        (" 4 , 2-3 ", 5, [4, 2, 3]),
        ("1,1", 1, [1, 1]),
    ],
)
def test_parse_page_range(spec, total, expected):
    assert parse_page_range(spec, total) == expected


@pytest.mark.parametrize("spec", ["0", "3-1", "1-11", "a", "1--2", "-", ",", "1,,2"])
def test_parse_page_range_rejects(spec):
    with pytest.raises(InvalidPrintOptions):
        parse_page_range(spec, 10)


def test_sheets_needed():
    assert sheets_needed(3, 1, duplex=False) == 3
    assert sheets_needed(3, 1, duplex=True) == 2
    assert sheets_needed(3, 2, duplex=True) == 4  # each copy starts on a new sheet
    assert sheets_needed(250, 1, duplex=True) == 125
    with pytest.raises(InvalidPrintOptions):
        sheets_needed(3, 0, duplex=False)
    with pytest.raises(InvalidPrintOptions):
        sheets_needed(3, 51, duplex=False)


def test_split_and_compartments():
    assert compartments_needed(500) == 1
    assert compartments_needed(501) == 2
    assert split_sheets(700) == [350, 350]
    assert split_sheets(1001) == [334, 334, 333]
    assert sum(split_sheets(1234)) == 1234
    assert all(p <= 500 for p in split_sheets(1234))


def test_plan_job_split():
    plan = plan_job(total_pages=700, page_range=None, copies=1, duplex=False)
    assert plan.sheets == 700 and plan.compartments == 2
    with pytest.raises(InvalidPrintOptions):
        plan_job(total_pages=700, page_range=None, copies=1, duplex=False, max_sheets=600)


# ---------- reservation ----------

def column(printer_id, states):
    return [
        CompartmentInfo(id=printer_id * 100 + i, printer_id=printer_id, slot_no=i, state=s)
        for i, s in enumerate(states, start=1)
    ]


def test_choose_lowest_free_slots():
    comps = column(1, [CS.OCCUPIED, CS.FREE, CS.RESERVED, CS.FREE, CS.FREE]) + column(2, [CS.FREE] * 5)
    picked = choose_compartments(comps, printer_id=1, needed=2)
    assert [c.slot_no for c in picked] == [2, 4]
    assert free_count(comps, 1) == 3


def test_reservation_ignores_other_printer_and_raises():
    comps = column(1, [CS.OCCUPIED, CS.FAULT, CS.EXPIRED]) + column(2, [CS.FREE] * 5)
    with pytest.raises(NoCompartmentAvailable) as e:
        choose_compartments(comps, printer_id=1, needed=1)
    assert e.value.free == 0


# ---------- PDF ----------

def test_pdf_page_count(pdf_bytes):
    assert count_pdf_pages(pdf_bytes(3)) == 3


def test_pdf_rejects_bad_files(pdf_bytes):
    with pytest.raises(InvalidPdf):
        count_pdf_pages(b"")
    with pytest.raises(InvalidPdf):
        count_pdf_pages(b"hello, not a pdf")
    with pytest.raises(InvalidPdf):
        count_pdf_pages(b"%PDF-1.7\n garbage garbage")
    with pytest.raises(InvalidPdf):
        count_pdf_pages(pdf_bytes(1), max_bytes=10)


def test_pdf_rejects_password_protected(pdf_bytes):
    from pypdf import PdfReader, PdfWriter

    w = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes(2))))
    w.encrypt(user_password="secret", algorithm="AES-128")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(InvalidPdf, match="password"):
        count_pdf_pages(buf.getvalue())


# ---------- codes ----------

def test_qr_roundtrip():
    token = new_qr_token()
    parsed = parse_qr(qr_payload(42, token))
    assert parsed.job_id == 42 and parsed.token == token
    for bad in ["", "PL1:x:y", "XX:1:abc", "PL1:1:", "PL1:1"]:
        with pytest.raises(ValueError):
            parse_qr(bad)


def test_hash_depends_on_kiosk_key():
    assert hash_code("k1", "123456") == hash_code("k1", "123456")
    assert hash_code("k1", "123456") != hash_code("k2", "123456")


def test_new_pin_avoids_taken():
    taken = {"000001"}
    pin = new_pin(lambda p: p in taken)
    assert len(pin) == 6 and pin.isdigit() and pin not in taken
    with pytest.raises(RuntimeError):
        new_pin(lambda p: True, attempts=3)


# ---------- storage ----------

def test_encrypted_store(tmp_path):
    store = EncryptedFileStore(tmp_path, EncryptedFileStore.new_key())
    name, sha = store.save(b"%PDF-1.7 data")
    assert b"PDF" not in (tmp_path / name).read_bytes()  # encrypted on disk
    assert store.load(name) == b"%PDF-1.7 data"
    assert len(sha) == 64
    store.delete(name)
    assert not store.exists(name)
    with pytest.raises(ValueError):
        store.load("../etc/passwd")
