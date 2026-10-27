"""Delivery of a generated message to a gateway's inbound folder."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.delivery import (  # noqa: E402
    DESTINATION_DEV,
    DESTINATION_DEV_POSTMARKET,
    DESTINATION_NONE,
    DESTINATIONS,
    DeliveryError,
    deliver,
    unique_name,
)


def test_the_post_market_destination_is_the_cdrh_inbound_folder():
    assert DESTINATIONS[DESTINATION_DEV_POSTMARKET].replace("\\", "/").endswith("inbound/cdrh/tt_3500A_emdr")


def test_dev_postmkt_writes_the_message(tmp_path):
    path = deliver("<a/>", DESTINATION_DEV_POSTMARKET, "26-002332CL Y.pdf", directory=str(tmp_path))
    assert os.path.basename(path).startswith("26-002332CL_Y-")
    assert open(path, encoding="utf-8").read() == "<a/>"


def test_none_delivers_nothing(tmp_path):
    assert deliver("<a/>", DESTINATION_NONE, "case.pdf", directory=str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []


def test_dev_writes_the_message_under_the_form_s_name(tmp_path):
    path = deliver("<a/>", DESTINATION_DEV, "/tmp/N141562-Jordan-Librela.pdf", directory=str(tmp_path))
    assert os.path.dirname(path) == str(tmp_path)
    name = os.path.basename(path)
    assert name.startswith("N141562-Jordan-Librela-") and name.endswith(".xml")
    assert open(path, encoding="utf-8").read() == "<a/>"


def test_every_run_of_a_form_takes_its_own_name(tmp_path):
    paths = {deliver("<a/>", DESTINATION_DEV, "case.pdf", directory=str(tmp_path)) for _ in range(5)}
    assert len(paths) == 5
    assert len(list(tmp_path.iterdir())) == 5


def test_a_name_carries_no_path_of_its_own():
    assert "/" not in unique_name("../../case with spaces.pdf")


def test_an_unwritable_destination_is_reported(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    with pytest.raises(DeliveryError):
        deliver("<a/>", DESTINATION_DEV, "case.pdf", directory=str(blocked))
    with pytest.raises(DeliveryError):
        deliver("<a/>", "prod", "case.pdf", directory=str(tmp_path))
