import json
import unittest
import tempfile
import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from treasure import (decode_response, run, load_config, account_sources,
                      run_accounts, write_reports, RewardHistory,
                      TaskError, QUERY, START, FINISH)


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
    def test_reward_history_current_month_deduplicates_serial(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rewards_history.json'
            history = RewardHistory(path, datetime(2026, 10, 8, tzinfo=timezone(timedelta(hours=8))))
            report = {'time': '2026-10-08T09:00:00+08:00', 'label': '账号 1',
                      'rounds': [{'map': '木叶物语', 'reward': '150点券,测试道具',
                                  'notice': '', 'serial': 'serial-1'}]}
            self.assertTrue(history.record_report(report, 'role|1'))
            self.assertFalse(history.record_report(report, 'role|1'))
            loaded = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(len(loaded['months']['2026-10']['records']), 1)
            text = history.markdown()
            self.assertIn('累计确认寻宝：1 次', text)
            self.assertIn('150点券：获得 1 次', text)

    def test_reward_history_compacts_previous_month(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rewards_history.json'
            path.write_text(json.dumps({'version': 1, 'months': {'2026-09': {'records': [
                {'id': 'old-1', 'reward': '150点券', 'items': ['150点券']},
                {'id': 'old-2', 'reward': '150点券,赛车', 'items': ['150点券', '赛车']}
            ]}}}, ensure_ascii=False), encoding='utf-8')
            history = RewardHistory(path, datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=8))))
            month = history.data['months']['2026-09']
            self.assertNotIn('records', month)
            self.assertEqual(month['summary']['rounds'], 2)
            self.assertEqual(month['summary']['items']['150点券'], 2)
            self.assertIn('2026-09', history.markdown())

    def test_run_accounts_records_rewards_even_when_account_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            history = RewardHistory(Path(folder) / 'rewards_history.json',
                                    datetime(2026, 10, 8, tzinfo=timezone(timedelta(hours=8))))
            def runner(client, mode, report):
                report['time'] = '2026-10-08T09:00:00+08:00'
                report['rounds'].append({'map': '木叶物语', 'reward': '150点券',
                                         'notice': '', 'serial': 'confirmed-1'})
                raise TaskError('后续状态校验失败')
            snapshots = []
            code = run_accounts([(1, lambda: {'role_id': 'one'})], 'all',
                                client_factory=lambda c: c, runner=runner,
                                sink=lambda reports, saved_history: snapshots.append(saved_history.markdown()),
                                history=history)
            self.assertEqual(code, 1)
            self.assertIn('150点券：获得 1 次', snapshots[-1])

    def test_five_secret_slots_and_empty_slots(self):
        env = {'SPEED_AUTH_JSON': '{"role_id":"one"}',
               'SPEED_AUTH_JSON_2': '', 'SPEED_AUTH_JSON_5': '{"role_id":"five"}'}
        with patch.dict('os.environ', env, clear=True):
            sources = account_sources()
            self.assertEqual([slot for slot, _ in sources], [1, 5])
            self.assertEqual([loader()['role_id'] for _, loader in sources], ['one', 'five'])
        env = {('SPEED_AUTH_JSON' if i == 1 else f'SPEED_AUTH_JSON_{i}'):
               json.dumps({'role_id': str(i)}) for i in range(1, 6)}
        with patch.dict('os.environ', env, clear=True):
            self.assertEqual(len(account_sources()), 5)

    def test_invalid_account_does_not_block_others(self):
        env = {'SPEED_AUTH_JSON': '{broken', 'SPEED_AUTH_JSON_2': '{"role_id":"two"}'}
        saved = []
        visited = []
        def runner(client, mode, report):
            visited.append(client['role_id'])
            report['status'] = '检查完成，未消耗次数'
        with patch.dict('os.environ', env, clear=True):
            code = run_accounts(account_sources(), 'inspect', client_factory=lambda c: c,
                                runner=runner, sink=lambda r: saved.append(copy.deepcopy(r)))
        self.assertEqual(code, 1)
        self.assertEqual(visited, ['two'])
        self.assertIn('error', saved[-1][0])
        self.assertNotIn('error', saved[-1][1])
        self.assertEqual(len(saved), 2)

    def test_multi_account_rewards_stay_separate_on_failure(self):
        sources = [(i, lambda i=i: {'role_id': str(i), 'label': f'测试{i}'}) for i in range(1, 4)]
        saved = []
        def runner(client, mode, report):
            report['rounds'].append({'map': '木叶物语', 'reward': f"奖励{client['role_id']}", 'notice': ''})
            if client['role_id'] == '2':
                raise TaskError('模拟网络失败')
            report['status'] = '单次寻宝完成'
        code = run_accounts(sources, 'once', client_factory=lambda c: c, runner=runner,
                            sink=lambda r: saved.append(copy.deepcopy(r)))
        self.assertEqual(code, 1)
        self.assertEqual([r['rounds'][0]['reward'] for r in saved[-1]], ['奖励1', '奖励2', '奖励3'])
        self.assertEqual(saved[-1][2]['status'], '单次寻宝完成')
        with patch('treasure.Path') as path, patch.dict('os.environ', {}, clear=True):
            write_reports(saved[-1])
            summary = path.return_value.write_text.call_args.args[0]
        self.assertIn('已处理账号：3；失败账号：1', summary)
        self.assertIn('账号 3', summary)
        self.assertIn('奖励1', summary)
        self.assertIn('奖励2', summary)
        self.assertIn('模拟网络失败', summary)

    def test_duplicate_account_does_not_run_twice(self):
        sources = [(1, lambda: {'role_id': 'same'}), (2, lambda: {'role_id': 'same'})]
        visited = []
        code = run_accounts(sources, 'inspect', client_factory=lambda c: c,
                            runner=lambda c, m, r: visited.append(c), sink=lambda r: None)
        self.assertEqual(code, 1)
        self.assertEqual(len(visited), 1)

    def test_all_success_and_maximum_local_configs(self):
        with self.assertRaises(TaskError):
            account_sources(['unused.json'] * 6)
        code = run_accounts([(1, lambda: {'role_id': 'one'})], 'inspect',
                            client_factory=lambda c: c,
                            runner=lambda c, m, r: r.update(status='检查完成'), sink=lambda r: None)
        self.assertEqual(code, 0)

    def test_local_config_bom_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'auth.json'
            path.write_text('{"role_id":"example"}', encoding='utf-8-sig')
            with patch.dict('os.environ', {'SPEED_AUTH_JSON': 'invalid'}):
                self.assertEqual(load_config(path)['role_id'], 'example')

    def test_environment_config_and_format_error(self):
        with patch.dict('os.environ', {'SPEED_AUTH_JSON': '{"role_id":"example"}'}):
            self.assertEqual(load_config()['role_id'], 'example')
        with patch.dict('os.environ', {'SPEED_AUTH_JSON': '{"role_id":"example",}'}):
            with self.assertRaisesRegex(TaskError, 'JSON 格式有误'):
                load_config()

    def test_chat_escaped_config(self):
        raw = '{\n&#x20; "access\\_token": "example",\n\u00a0 "role\\_id": "test"\n}'
        with patch.dict('os.environ', {'SPEED_AUTH_JSON': raw}):
            self.assertEqual(load_config(), {'access_token': 'example', 'role_id': 'test'})

    def test_valid_string_values_are_preserved(self):
        values = {'text': 'a\u00a0b &#x20; &nbsp;', 'escaped': r'a\_b', 'quote': 'a"b'}
        with patch.dict('os.environ', {'SPEED_AUTH_JSON': json.dumps(values)}):
            self.assertEqual(load_config(), values)
        # 只修复字符串外的特殊空格，保留字符串内的 HTML 字面量和合法转义。
        raw = '\u00a0' + json.dumps(values)
        with patch.dict('os.environ', {'SPEED_AUTH_JSON': raw}):
            self.assertEqual(load_config(), values)

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
