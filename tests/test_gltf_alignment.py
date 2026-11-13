"""Verify glTF mesh buffer alignment and data across consecutive meshes."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from application import config  # noqa: F401
from jt.gltf_writer import write_gltf


class BufferAlignmentTests(unittest.TestCase):
    def test_consecutive_meshes_preserve_data_and_four_byte_alignment(self):
        """Odd 16-bit index counts must not misalign the next mesh.

        > example
        > A one-triangle mesh followed by another mesh writes aligned views.
        """
        points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
        normals = [(0, 0, 1)] * 3
        cases = [
            {"pts": points, "tris": [(0, 1, 2)]},
            {"pts": points, "tris": [(0, 1, 2), (1, 3, 2)]},
            {"pts": points, "tris": [(0, 1, 2)], "cornerNormals": normals},
            {"pts": [(0, 0, 0)] * 65537, "tris": [(0, 1, 65536)]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            for index, first in enumerate(cases):
                with self.subTest(case=index):
                    stem = str(Path(directory) / f"model-{index}")
                    second = {"pts": points, "tris": [(2, 1, 3)]}
                    document = write_gltf(
                        [{"t": first}, {"t": second}], stem, glb=True)
                    binary = Path(stem + ".bin").read_bytes()
                    glb = Path(stem + ".glb").read_bytes()
                    json_length = struct.unpack_from("<I", glb, 12)[0]
                    glb_doc = json.loads(glb[20:20 + json_length])
                    bin_offset = 20 + json_length
                    bin_length, bin_type = struct.unpack_from("<II", glb, bin_offset)
                    self.assertEqual(bin_type, 0x004E4942)
                    self.assertEqual(glb[bin_offset + 8:bin_offset + 8 + bin_length],
                                     binary)
                    self.assertEqual(document["bufferViews"], glb_doc["bufferViews"])
                    self.assertEqual(document["buffers"][0]["byteLength"], len(binary))
                    self.assertTrue(all(view["byteOffset"] % 4 == 0
                                        for view in document["bufferViews"]))
                    for mesh, expected in zip(document["meshes"], (first, second)):
                        primitive = mesh["primitives"][0]
                        accessor = document["accessors"][primitive["indices"]]
                        view = document["bufferViews"][accessor["bufferView"]]
                        index_format = "I" if accessor["componentType"] == 5125 else "H"
                        actual_indices = struct.unpack_from(
                            "<" + index_format * accessor["count"], binary,
                            view["byteOffset"])
                        self.assertEqual(actual_indices,
                                         tuple(i for tri in expected["tris"] for i in tri))
                        position = document["accessors"][primitive["attributes"]["POSITION"]]
                        position_view = document["bufferViews"][position["bufferView"]]
                        actual_first = struct.unpack_from(
                            "<3f", binary, position_view["byteOffset"])
                        self.assertEqual(actual_first, expected["pts"][0])


if __name__ == "__main__":
    unittest.main()
