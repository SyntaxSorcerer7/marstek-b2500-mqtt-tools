import unittest

import hmj2_mqtt_tool as tool


def complete_status():
    data = {"md": "1", "cs": "0"}
    for slot in range(1, 6):
        data.update({
            f"d{slot}": "0",
            f"e{slot}": "0:0",
            f"f{slot}": "23:59",
            f"h{slot}": str(slot * 100),
        })
    return data


class ToolTests(unittest.TestCase):
    def test_mac_helpers_accept_a_generic_example(self):
        mac = tool.normalize_mac("aa:bb-cc dd ee ff")
        self.assertEqual(mac, "AABBCCDDEEFF")
        self.assertTrue(tool.valid_mac(mac))
        self.assertEqual(tool.format_mac(mac), "AA:BB:CC:DD:EE:FF")

    def test_payload_parser_ignores_invalid_entries(self):
        self.assertEqual(
            tool.parse_payload("pe=4, vv=116, malformed, sg = 1"),
            {"pe": "4", "vv": "116", "sg": "1"},
        )

    def test_schedule_preserves_other_slots(self):
        original = complete_status()
        payload, expected = tool.build_output_schedule(
            original, 2, 1, "08:00", "18:30", 450
        )
        self.assertIn("cd=07,md=1", payload)
        self.assertIn("a2=1,b2=8:0,e2=18:30,v2=450", payload)
        self.assertIn("a5=0,b5=0:0,e5=23:59,v5=500", payload)
        self.assertEqual(expected["h2"], "450")
        self.assertEqual(original, complete_status())

    def test_schedule_rejects_invalid_values(self):
        for start, end, watts in (("25:00", "26:00", 100), ("18:00", "8:00", 100), ("8:00", "9:00", 801)):
            with self.subTest(start=start, end=end, watts=watts):
                with self.assertRaises(ValueError):
                    tool.build_output_schedule(complete_status(), 1, 1, start, end, watts)


if __name__ == "__main__":
    unittest.main()
