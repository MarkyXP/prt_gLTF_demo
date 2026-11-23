"""Synthetic tests for body-colour row heads and OM int tag descriptors."""

import unittest
from unittest.mock import patch

from application import config  # noqa: F401

from color_index import (
    _body_tag,
    _decode_tag_token,
    body_colour_by_tag,
    colour_sites,
)


def body_row(head, palette, descriptor):
    """Build one body colour record around a palette token and descriptor.

    > example
    > body_row(b"\\xc0\\x40\\x04\\x00", b"\\x05", b"\\x02\\x08")[:8]
    > -> b'G\\x01\\x04\\x01\\xc0@\\x04'
    """
    return (b"\x47\x01\x04\x01" + head + palette + descriptor
            + b"\x01\x93\x8e")


class NewBodyHeadTests(unittest.TestCase):
    def test_head_40_04_is_a_body_colour_site(self):
        payload = body_row(b"\xc0\x40\x04\x00", b"\x05", b"\x02\x08")
        self.assertEqual(
            [(site.token, site.kind) for site in colour_sites(payload)],
            [(4, "body")])

    def test_head_43_04_is_a_body_colour_site(self):
        payload = body_row(b"\xc0\x43\x04\x00", b"\x0c", b"\x02\x0a")
        self.assertEqual(
            [(site.token, site.kind) for site in colour_sites(payload)],
            [(11, "body")])

    def test_legacy_4_byte_descriptor_stays_rejected(self):
        payload = body_row(
            b"\xc0\x40\x84\x00", b"\x10", b"\x81\x0f\x84\x9f")
        self.assertEqual(colour_sites(payload), [])


class BodyTagDescriptorTests(unittest.TestCase):
    def test_plain_tag(self):
        self.assertEqual(_body_tag(b"\x02\x08\x01\x93\x8e", 0), 8)

    def test_two_byte_wide_tag(self):
        self.assertEqual(_body_tag(b"\x02\x80\x8b\x01\x93\x8e", 0), 139)

    def test_prefix_token_81_0f(self):
        self.assertEqual(_body_tag(b"\x81\x0f\x0d\x01\x93\x8e", 0), 13)
        self.assertEqual(_body_tag(b"\x81\x0f\x0c\x01\x93\x8e", 0), 12)

    def test_three_byte_wide_token(self):
        self.assertEqual(
            _decode_tag_token(b"\x9a\x01\x02", 0), (0x0A * 65536 + 0x0102, 3))

    def test_truncated_descriptor_fails_closed(self):
        self.assertIsNone(_body_tag(b"\x02\x08\x01\x93", 0))
        self.assertIsNone(_body_tag(b"\x02\x80", 0))
        self.assertIsNone(_body_tag(b"\x02\x08", 5))

    def test_invalid_token_byte_fails_closed(self):
        self.assertIsNone(_body_tag(b"\xb0\x08\x01\x93\x8e", 0))

    def test_truncated_legacy_row_is_not_a_site(self):
        payload = b"\x47\x01\x04\x01\xc0\x40\x04\x00\x05\x02\x08\x01\x93"
        self.assertEqual(colour_sites(payload), [])


class BodyTagBindingTests(unittest.TestCase):
    def _records(self, payload):
        table = {
            4: (1.0, 0.0, 0.0),
            11: (0.8, 1.0, 0.2),
            142: (0.0, 0.4, 0.4),
        }
        with patch("color_index.find_ug_part", return_value=payload), \
             patch("converter.palette.palette_table", return_value=table):
            return body_colour_by_tag("part.prt")

    def test_new_heads_and_prefixed_tags_bind_once_each(self):
        payload = (
            body_row(b"\xc0\x40\x04\x00", b"\x05", b"\x02\x08")
            + body_row(b"\xc0\x44\x04\x00", b"\x80\x8e", b"\x81\x0f\x0d")
            + body_row(b"\xc0\x43\x04\x00", b"\x0c", b"\x02\x0a")
        )
        records = self._records(payload)
        self.assertEqual(sorted(records), [8, 10, 13])
        self.assertEqual(records[8]["palette"], 4)
        self.assertEqual(records[13]["palette"], 142)
        self.assertEqual(records[10]["palette"], 11)
        self.assertEqual(colour_sites(payload)[0].offset, 4)

    def test_wide_tag_binds_as_raw_value(self):
        payload = body_row(b"\xc0\x44\x04\x00", b"\x80\x8e", b"\x02\x80\x8b")
        records = self._records(payload)
        self.assertEqual(sorted(records), [139])
        self.assertEqual(records[139]["palette"], 142)

    def test_headless_tail_row_does_not_bind(self):
        payload = b"\x47\x01\x04\x01\xc0\x44\x04\x00\x05\x02\x08"
        self.assertEqual(self._records(payload), {})

    def test_malformed_token_row_does_not_bind(self):
        payload = body_row(b"\xc0\x44\x04\x00", b"\x05", b"\xb0\x08")
        self.assertEqual(self._records(payload), {})


if __name__ == "__main__":
    unittest.main()
