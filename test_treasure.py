import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from treasure import decode_response, run, load_config, TaskError, QUERY, START, FINISH


def state(remain, daji=True):
    return {'ret': 0, 'remain': remain, 'mapId': '', 'remainingTime': 0,
            'mapList': [{'star_level': 4, 'map_info': [
                {'map_id': 'map4_3', 'name': '木叶物语', 'daji': int(daji)}]}]}


def start():
    return {'ret': 0, 'mapId': 'map4_3', 'expireTime': 1010,
            'holdMatch': 0, 'isUnLock': 0, 'isInProgress': 0}


def reward():
    return {'ret': 0, 'ams_code': '0', 'package_name': '测试道具(7天),150点券'}


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def call(self, flow, extra=None):
        self.calls.append((flow, extra))
        expected_flow, result = next(self.responses)
        assert flow == expected_flow
        if isinstance(result, Exception):
            raise result
        return result


class Tests(unittest.TestCase):
    def test_local_config_bom_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'auth.json'
            path.write_text('{"role_id":"example"}', encoding='utf-8-sig')
            with patch.dict('os.environ', {'SPEED_AUTH_JSON': 'invalid'}):
                self.assertEqual(load_config(path)['role_id'], 'example')

    def test_environment_config_and_format_error(self):
        with patch.dict('os.environ', {'SPEED_AUTH_JSON': '{"role_id":"example"}'}):
            self.assertEqual(load_config()['role_id'], 'example')
        with patch.dict('os.environ', {'SPEED_AUTH_JSON': '{"role\\_id":"example"}'}):
            with self.assertRaisesRegex(TaskError, 'JSON 格式有误'):
                load_config()

    def test_missing_local_config(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(TaskError, '未找到配置'):
                load_config(Path(folder) / 'missing.json')

    def execute(self, client, mode='all'):
        report = {'rounds': []}
        run(client, mode, report, sleep=lambda _: None, now=lambda: 1000)
        return report

    def test_two_rounds_stop_at_zero(self):
        c = FakeClient([(QUERY, state(2)), (START, start()), (FINISH, reward()),
                        (QUERY, state(1)), (START, start()), (FINISH, reward()),
                        (QUERY, state(0))])
        report = self.execute(c)
        self.assertEqual(report['remaining'], 0)
        self.assertEqual(len(report['rounds']), 2)
        self.assertEqual(c.calls[1][1], {'starLevel': 4, 'StarLevel': 4,
                                       'mapId': 'map4_3', 'MapID': 'map4_3'})

    def test_inspect_never_starts(self):
        c = FakeClient([(QUERY, state(3))])
        self.execute(c, 'inspect')
        self.assertEqual(len(c.calls), 1)

    def test_zero_never_starts(self):
        c = FakeClient([(QUERY, state(0))])
        self.execute(c)
        self.assertEqual(len(c.calls), 1)

    def test_missing_lucky_map_stops(self):
        c = FakeClient([(QUERY, state(3, False))])
        with self.assertRaises(TaskError):
            self.execute(c)
        self.assertEqual(len(c.calls), 1)

    def test_uncertain_start_is_not_repeated(self):
        c = FakeClient([(QUERY, state(3)), (START, TaskError('timeout'))])
        with self.assertRaises(TaskError):
            self.execute(c)
        self.assertEqual([x[0] for x in c.calls], [QUERY, START])

    def test_uncertain_finish_is_not_repeated(self):
        c = FakeClient([(QUERY, state(3)), (START, start()), (FINISH, TaskError('timeout'))])
        with self.assertRaises(TaskError):
            self.execute(c)
        self.assertEqual([x[0] for x in c.calls], [QUERY, START, FINISH])

    def test_unchanged_count_stops_and_preserves_reward(self):
        c = FakeClient([(QUERY, state(3)), (START, start()), (FINISH, reward()),
                        (QUERY, state(3)), (QUERY, state(3)), (QUERY, state(3))])
        report = {'rounds': []}
        with self.assertRaises(TaskError):
            run(c, 'all', report, sleep=lambda _: None, now=lambda: 1000)
        self.assertEqual(len(report['rounds']), 1)

    def test_nested_json_and_business_failure(self):
        self.assertEqual(decode_response(json.dumps({'ret': 0, 'data': json.dumps(state(3))}))['remain'], 3)
        with self.assertRaises(TaskError):
            decode_response('{"ret":0,"data":"{\\"ret\\":1}"}')

    def test_existing_round_stops(self):
        active = state(3)
        active.update(mapId='map4_3', remainingTime=5)
        c = FakeClient([(QUERY, active)])
        with self.assertRaises(TaskError):
            self.execute(c)
        self.assertEqual(len(c.calls), 1)


if __name__ == '__main__':
    unittest.main()
