import unittest

from src.tools.gen_beacon_labels import (
    UUID_CONST,
    build_html,
    device_name,
    load_site,
    precheck,
)


def beacon(beacon_id="BK-01-001", floor="1", major=1, minor=10101):
    return {
        "beaconId": beacon_id,
        "floorId": floor,
        "major": major,
        "minor": minor,
        "uuid": UUID_CONST,
        "locationDesc": "1F 电梯口",
    }


class TestBeaconLabels(unittest.TestCase):
    def test_device_name_rule_and_length(self):
        self.assertEqual(device_name(beacon(minor=10101)), "F1-101")
        self.assertEqual(device_name(beacon(beacon_id="BK-M-2-001", floor="2", minor=20001)), "F2-001")

    def test_real_map_package_labels(self):
        pkg = load_site("school-building-01", "latest")
        beacons = pkg["beacons"]
        precheck(beacons)
        html_text = build_html(pkg, beacons)
        self.assertEqual(html_text.count('class="label"'), len(beacons))
        self.assertEqual(html_text.count("<svg"), len(beacons))

    def test_precheck_rejects_bad_beacon_id(self):
        with self.assertRaises(SystemExit):
            precheck([beacon(beacon_id="bad id!")])

    def test_precheck_rejects_duplicate_triplet(self):
        with self.assertRaises(SystemExit):
            precheck([beacon(), beacon(beacon_id="BK-01-002")])

    def test_precheck_rejects_duplicate_device_name(self):
        # 不同 major 但同层同 minor 后三位 → 短码冲突
        with self.assertRaises(SystemExit):
            precheck([beacon(), beacon(beacon_id="BK-02-001", major=2, minor=11101)])

    def test_precheck_rejects_uuid_mismatch(self):
        item = beacon()
        item["uuid"] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaises(SystemExit):
            precheck([item])


if __name__ == "__main__":
    unittest.main()
