from __future__ import annotations

import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError


class BitrixReadTest(unittest.TestCase):
    def setUp(self):
        from bitrix_mcp import disk, modules, read, write

        self.api, self.disk, self.write, self.modules = read, disk, write, modules
        self.secret = Mock(return_value="https://example.bitrix24.ru/rest/1/synthetic-key/")
        secret_patch = patch.object(read, "_webhook", self.secret)
        secret_patch.start()
        self.addCleanup(secret_patch.stop)
        self.opener = Mock()
        self.transport = patch.object(self.api, "build_opener", return_value=self.opener)
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def response(self, data):
        response = io.BytesIO(json.dumps(data).encode())
        self.opener.open.return_value = response

    def test_catalog_and_invalid_input_do_not_access_credentials(self):
        self.assertTrue(self.api.main()["read_only"])
        for args in [
            {"operation": "tasks.task.delete"},
            {"operation": "tasks", "filters": {"auth": "override"}},
            {"operation": "tasks", "filters": {"TITLE": {"nested": "object"}}},
            {"operation": "tasks", "filters": {"RESPONSIBLE_ID": True}},
            {"operation": "tasks", "filters": {"DEADLINE": "2026-10-30T12:00:00"}},
            {"operation": "tasks", "start": -1},
            {"operation": "tasks", "start": 2},
            {"operation": "tasks", "include_description": True},
            {"operation": "task"},
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.api.main(**args)
        self.secret.assert_not_called()
        self.opener.open.assert_not_called()

    def test_webhook_cannot_change_host_path_query_or_credentials(self):
        for url in [
            "http://example.bitrix24.ru/rest/1/synthetic-key/",
            "https://evil.test/rest/1/synthetic-key/",
            "https://example.bitrix24.ru@evil.test/rest/1/synthetic-key/",
            "https://example.bitrix24.ru/rest/1/synthetic-key/profile.json",
            "https://example.bitrix24.ru/rest/1/synthetic-key/?url=evil",
        ]:
            self.secret.return_value = url
            with self.assertRaisesRegex(ValueError, "bitrix_base_webhook_required"):
                self.api.main("profile")
        self.opener.open.assert_not_called()

    def test_page_is_read_only_minimized_and_explicitly_incomplete(self):
        self.response(
            {
                "result": {
                    "tasks": [
                        {
                            "id": "42",
                            "title": "Task",
                            "description": "hidden",
                            "creator": {"email": "hidden"},
                            "status": "3",
                        }
                    ]
                },
                "total": 102,
                "next": 50,
            }
        )
        result = self.api.main("tasks", {"RESPONSIBLE_ID": 101, "!REAL_STATUS": 5})
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["total_matching"], 102)
        self.assertEqual(result["next_start"], 50)
        self.assertFalse(result["complete"])
        self.assertNotIn("description", result["tasks"][0])
        self.assertNotIn("creator", result["tasks"][0])
        request = self.opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/tasks.task.list.json"))
        body = json.loads(request.data)
        self.assertEqual(body["filter"], {"RESPONSIBLE_ID": 101, "!REAL_STATUS": 5})
        self.assertEqual(body["order"], {"ID": "ASC"})
        self.assertNotIn("DESCRIPTION", body["select"])
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertIsNone(self.api.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_last_page_is_not_mistaken_for_whole_result(self):
        self.response({"result": {"tasks": [{"id": "1"}]}, "total": 101})
        self.assertFalse(self.api.main("tasks", start=100)["complete"])
        self.response({"result": {"tasks": []}, "total": 0})
        self.assertTrue(self.api.main("tasks")["complete"])
        self.response({"result": {"tasks": []}})
        self.assertFalse(self.api.main("tasks")["complete"])

    def test_detail_uses_list_and_redacts_and_marks_truncated_text(self):
        self.response({"result": {"tasks": [{"id": "42", "description": "synthetic-key " + "x" * 13000}]}, "total": 1})
        result = self.api.main("task", task_id=42, include_description=True)
        task = result["tasks"][0]
        self.assertNotIn("synthetic-key", task["description"])
        self.assertEqual(task["truncated_fields"], ["description"])
        request = self.opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/tasks.task.list.json"))
        self.assertEqual(json.loads(request.data)["filter"], {"ID": 42})

    def test_failures_do_not_echo_secret_or_payload(self):
        failures = [
            URLError("synthetic-key"),
            HTTPError("https://example.test/synthetic-key", 403, "synthetic-key", {}, None),
        ]
        for failure in failures:
            self.opener.open.side_effect = failure
            with self.assertRaises(RuntimeError) as caught:
                self.api.main("profile")
            self.assertNotIn("synthetic-key", str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)
        self.opener.open.side_effect = None
        self.response({"error": "synthetic-key", "error_description": "private payload"})
        with self.assertRaisesRegex(RuntimeError, "^bitrix_api_error$"):
            self.api.main("profile")

    def test_malformed_and_oversized_responses_fail_closed(self):
        for data in [
            {"result": []},
            {"result": {"tasks": [{}]}},
            {"result": {"tasks": []}, "next": 0},
            {"result": {"tasks": []}, "total": "secret"},
        ]:
            self.response(data)
            with self.assertRaises(RuntimeError):
                self.api.main("tasks")
        self.opener.open.return_value = io.BytesIO(b"x" * (self.api.MAX_BYTES + 1))
        with self.assertRaisesRegex(RuntimeError, "bitrix_response_too_large"):
            self.api.main("profile")
        self.response({"result": {"tasks": [{"id": "99"}]}})
        with self.assertRaisesRegex(RuntimeError, "bitrix_task_id_mismatch"):
            self.api.main("task", task_id=42)

    def test_write_plan_does_not_use_secret_and_rejects_extra_rights_or_author(self):
        planned = self.write.main("create_task", {"fields": {"TITLE": "Test", "RESPONSIBLE_ID": 101}})
        self.assertEqual(planned["outcome"], "planned")
        self.assertFalse(planned["mutated"])
        for operation, parameters in [
            ("delete_task", {"task_id": 42}),
            ("comment", {"task_id": 42, "message": "Test", "AUTHOR_ID": 1}),
            ("create_task", {"fields": {"TITLE": "Test", "RESPONSIBLE_ID": 101, "CREATED_BY": 1}}),
            ("update_task", {"task_id": 42, "fields": {"STATUS": 5}}),
            ("create_folder", {"parent_id": 1, "parent_type": "storage", "name": "Test", "rights": []}),
            ("upload_file", {"parent_id": 1, "parent_type": "folder", "name": "../Test", "content_base64": ""}),
        ]:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.write.main(operation, parameters, apply=True)
        self.secret.assert_not_called()
        self.opener.open.assert_not_called()

    def test_write_apply_uses_one_allowed_request_and_minimal_receipt(self):
        self.response({"result": {"task": {"id": "42", "secret": "hidden"}}})
        result = self.write.main("create_task", {"fields": {"TITLE": "Test", "RESPONSIBLE_ID": 101}}, apply=True)
        self.assertEqual(result["outcome"], "applied")
        self.assertEqual(result["receipt"], {"task_id": 42})
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertTrue(self.opener.open.call_args.args[0].full_url.endswith("/tasks.task.add.json"))
        self.response({"result": 12})
        self.write.main("comment", {"task_id": 42, "message": "Approved text"}, apply=True)
        self.assertEqual(
            json.loads(self.opener.open.call_args.args[0].data),
            {"TASKID": 42, "FIELDS": {"POST_MESSAGE": "Approved text"}},
        )

    def test_write_unknown_outcome_never_retries_or_claims_failure_without_mutation(self):
        self.opener.open.side_effect = URLError("secret-token")
        result = self.write.main("complete_task", {"task_id": 42}, apply=True)
        self.assertEqual(result["outcome"], "unknown")
        self.assertIsNone(result["mutated"])
        self.assertFalse(result["retry_safe"])
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertNotIn("secret-token", str(result))
        self.opener.open.side_effect = None
        self.response({"result": {"task": {"id": 99}}})
        self.assertEqual(
            self.write.main("update_task", {"task_id": 42, "fields": {"TITLE": "Test"}}, apply=True)["outcome"],
            "unknown",
        )

    def test_upload_plan_omits_contents_and_request_does_not_change_rights(self):
        p = {"parent_id": 10, "parent_type": "folder", "name": "test.txt", "content_base64": "aGVsbG8="}
        plan = self.write.main("upload_file", p)
        self.assertEqual(plan["changes"]["bytes"], 5)
        self.assertNotIn("aGVsbG8=", str(plan))
        self.response({"result": {"ID": "123", "DOWNLOAD_URL": "secret"}})
        result = self.write.main("upload_file", p, apply=True)
        self.assertEqual(result["receipt"], {"disk_object_id": 123})
        body = json.loads(self.opener.open.call_args.args[0].data)
        self.assertFalse(body["generateUniqueName"])
        self.assertNotIn("rights", body)

    def test_disk_metadata_drops_download_urls_and_search_reports_cap(self):
        self.response({"result": [{"ID": "12", "NAME": "Test", "DOWNLOAD_URL": "secret"}], "total": 1})
        result = self.disk.main("storages")
        self.assertTrue(result["complete"])
        self.assertNotIn("DOWNLOAD_URL", str(result))
        self.response({"result": [{"ID": "12"}], "next": 1050})
        result = self.disk.main("search", query="Отчёт", start=1000)
        self.assertFalse(result["complete"])
        self.assertTrue(result["search_limit_reached"])
        self.assertIsNone(result["next_start"])

    def test_disk_download_rejects_other_hosts_routes_and_large_files(self):
        for url, size in [
            ("https://evil.test/rest/download.json?auth=secret", 5),
            ("https://example.bitrix24.ru/rest/1/secret/task.delete.json", 5),
            ("https://example.bitrix24.ru/rest/download.json?auth=secret", 3000000),
        ]:
            with self.subTest(url=url), self.assertRaises((RuntimeError, ValueError)):
                self.disk._download({"SIZE": str(size), "DOWNLOAD_URL": url}, "text", "synthetic-key")
        self.opener.open.assert_not_called()
        with patch.object(self.disk, "build_opener", return_value=self.opener):
            self.opener.open.return_value = io.BytesIO(b"hello")
            result = self.disk._download(
                {"SIZE": "5", "DOWNLOAD_URL": "https://example.bitrix24.ru/rest/download.json?auth=secret"},
                "text",
                "synthetic-key",
            )
        self.assertEqual(result["content"], "hello")
        self.assertNotIn("auth=secret", str(result))
        with patch.object(self.disk, "build_opener", return_value=self.opener):
            self.opener.open.return_value = io.BytesIO(b"synthetic-key")
            with self.assertRaisesRegex(RuntimeError, "contains_connection_secret"):
                self.disk._download(
                    {"SIZE": "13", "DOWNLOAD_URL": "https://example.bitrix24.ru/rest/download.json?auth=secret"},
                    "base64",
                    "synthetic-key",
                )

    def test_every_state_and_disk_move_uses_exact_target(self):
        for operation, method in self.write.TASK_STATES.items():
            with self.subTest(operation=operation):
                self.response({"result": True})
                result = self.write.main(operation, {"task_id": 42}, apply=True)
                self.assertEqual(result["receipt"], {"task_id": 42})
                request = self.opener.open.call_args.args[0]
                self.assertTrue(request.full_url.endswith("/" + method + ".json"))
                self.assertEqual(json.loads(request.data), {"taskId": 42})
        for kind in ["file", "folder"]:
            self.response({"result": {"ID": "12"}})
            self.write.main("move_" + kind, {"object_id": 12, "target_folder_id": 34}, apply=True)
            self.assertEqual(json.loads(self.opener.open.call_args.args[0].data), {"id": 12, "targetFolderId": 34})
            self.response({"result": {"ID": "12"}})
            self.write.main("rename_" + kind, {"object_id": 12, "name": "new.txt"}, apply=True)
            self.assertEqual(json.loads(self.opener.open.call_args.args[0].data), {"id": 12, "newName": "new.txt"})
        with self.assertRaisesRegex(ValueError, "bitrix_method_not_allowed"):
            self.api._request("https://example.bitrix24.ru/", "tasks.task.add", {})

    def test_classic_comments_hide_email_and_do_not_claim_chat_coverage(self):
        self.response({"result": [{"ID": "12", "POST_MESSAGE": "hello", "AUTHOR_EMAIL": "private"}]})
        result = self.api.main("comments", task_id=42)
        self.assertEqual(result["returned"], 1)
        self.assertFalse(result["complete"])
        self.assertNotIn("AUTHOR_EMAIL", str(result))

    def test_portal_read_write_sets_are_disjoint_and_scoped(self):
        self.assertFalse(set(self.modules.API_READ) & set(self.modules.API_WRITE))
        self.assertTrue(set(self.modules.API_READ) <= self.api.READ_METHODS)
        self.assertTrue(set(self.modules.API_WRITE) <= self.api.WRITE_METHODS)
        for forbidden in (
            "batch",
            "user.update",
            "disk.file.delete",
            "log.blogpost.share",
            "tasks_extended.meta.occurinlogsas",
        ):
            with self.subTest(method=forbidden), self.assertRaises(ValueError):
                self.api.main("api", method=forbidden, parameters={})
        self.secret.assert_not_called()

    def test_portal_api_rejects_credentials_actor_and_hidden_writes_before_secret(self):
        bad_reads = [
            ("im.dialog.read", {"DIALOG_ID": "123"}),
            ("im.message.add", {"DIALOG_ID": "123", "MESSAGE": "x"}),
            ("user.get", {"auth": "override"}),
            ("user.get", {"FILTER": {"auth": "override"}}),
            ("im.dialog.messages.get", {"DIALOG_ID": "chat12", "LIMIT": 51}),
            ("im.dialog.messages.get", {"DIALOG_ID": "chat12", "FIRST_ID": 1, "LAST_ID": 2}),
            ("im.notify.get", {"LAST_ID": 5}),
            ("calendar.event.get", {"type": "user", "ownerId": 101}),
        ]
        for method, parameters in bad_reads:
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.api.main("api", method=method, parameters=parameters)
        bad_writes = [
            ("im.message.update", {"MESSAGE_ID": 1, "MESSAGE": " "}),
            ("im.message.like", {"MESSAGE_ID": 1, "ACTION": "auto"}),
            ("im.notify", {"USER_ID": 1, "MESSAGE": "x", "TAG": "replace-and-delete"}),
            ("im.message.add", {"DIALOG_ID": "1", "MESSAGE": "x", "SYSTEM": "Y"}),
            ("log.blogpost.add", {"POST_TITLE": "x", "POST_MESSAGE": "y"}),
            ("log.blogpost.add", {"POST_TITLE": "x", "POST_MESSAGE": "y", "DEST": ["U1"], "USER_ID": 99}),
            ("log.blogpost.update", {"POST_ID": 1, "POST_MESSAGE": "x"}),
            ("task.elapseditem.add", {"TASKID": 1, "ARFIELDS": {"SECONDS": 60, "USER_ID": 99}}),
        ]
        for method, arguments in bad_writes:
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.write.main("api", {"method": method, "arguments": arguments}, apply=True)
        self.secret.assert_not_called()
        self.opener.open.assert_not_called()

    def test_portal_calendar_has_explicit_dates_and_bounded_window(self):
        args = {"type": "user", "ownerId": 101, "from": "2026-10-30", "to": "2026-11-02"}
        self.assertEqual(self.modules.validate_api("calendar.event.get", args), args)
        for change in ({"to": "2027-11-02"}, {"to": "2026-10-29"}, {"ownerId": 0}, {"from": "2026-10-30T09:00:00"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.modules.validate_api("calendar.event.get", {**args, **change})
        event = {**args, "section": 5, "name": "Meeting"}
        with self.assertRaisesRegex(ValueError, "skip_time"):
            self.modules.validate_api("calendar.event.add", event, mutation=True)
        event["skip_time"] = "Y"
        self.modules.validate_api("calendar.event.add", event, mutation=True)
        update = {"id": 1, "type": "user", "ownerId": 101, "name": "Title"}
        with self.assertRaises(ValueError):
            self.modules.validate_api("calendar.event.update", update, mutation=True)
        with self.assertRaisesRegex(ValueError, "recurrence_instance_date_required"):
            self.modules.validate_api("calendar.event.update", {**update, "recurrence_mode": "this"}, mutation=True)

    def test_portal_read_preserves_cursors_and_redacts_nested_secrets(self):
        self.response(
            {
                "result": {
                    "messages": [
                        {"id": 7, "text": "synthetic-key", "DOWNLOAD_URL": "secret-url", "nested": {"auth": "x"}}
                    ],
                    "hasMore": True,
                },
                "next": 50,
                "total": 120,
            }
        )
        result = self.api.main("api", method="im.dialog.messages.get", parameters={"DIALOG_ID": "chat8", "LAST_ID": 9})
        self.assertEqual(result["pagination"], {"next": 50, "total": 120})
        self.assertFalse(result["complete"])
        self.assertTrue(result["result"]["hasMore"])
        self.assertNotIn("synthetic-key", str(result))
        self.assertNotIn("DOWNLOAD_URL", str(result))
        self.assertEqual(
            json.loads(self.opener.open.call_args.args[0].data), {"DIALOG_ID": "chat8", "LAST_ID": 9, "LIMIT": 50}
        )

    def test_basic_user_scope_returns_directory_fields_only(self):
        self.response(
            {
                "result": [
                    {"ID": "1", "NAME": "User", "UF_DEPARTMENT": [2], "EMAIL": "private", "PERSONAL_PHONE": "private"}
                ],
                "total": 1,
            }
        )
        result = self.api.main("api", method="user.get", parameters={"FILTER": {"ID": 1}})
        self.assertEqual(result["result"], [{"ID": "1", "NAME": "User", "UF_DEPARTMENT": [2]}])

    def test_portal_output_truncation_is_explicit(self):
        result, paths = self.modules.sanitize_result(
            {"text": "x" * 12001, "items": list(range(201)), "token": "hidden"}, "synthetic-key"
        )
        self.assertEqual(len(result["text"]), 12000)
        self.assertEqual(len(result["items"]), 200)
        self.assertEqual(set(paths), {"result.text", "result.items"})
        self.assertNotIn("token", result)

    def test_each_portal_write_has_offline_plan_and_explicit_target(self):
        event = {
            "type": "user",
            "ownerId": 101,
            "from": "2026-11-01T12:00:00+03:00",
            "to": "2026-11-01T13:00:00+03:00",
            "section": 12,
            "name": "Meeting",
        }
        examples = {
            "calendar.event.add": event,
            "calendar.event.update": {
                "id": 1,
                "type": "user",
                "ownerId": 101,
                "name": "Updated",
                "recurrence_mode": "this",
                "current_date_from": "2026-11-01",
            },
            "calendar.meeting.status.set": {"eventId": 1, "status": "Y"},
            "im.message.add": {"DIALOG_ID": "chat1", "MESSAGE": "Message"},
            "im.message.update": {"MESSAGE_ID": 1, "MESSAGE": "Updated"},
            "im.message.like": {"MESSAGE_ID": 1, "ACTION": "plus"},
            "im.notify": {"USER_ID": 1, "MESSAGE": "Notice"},
            "log.blogpost.add": {"POST_TITLE": "Title", "POST_MESSAGE": "Body", "DEST": ["U1"]},
            "log.blogpost.update": {"POST_ID": 1, "POST_TITLE": "Title", "POST_MESSAGE": "Body"},
            "log.blogcomment.add": {"POST_ID": 1, "TEXT": "Comment"},
            "department.add": {"NAME": "Department", "PARENT": 1},
            "department.update": {"ID": 1, "NAME": "Department"},
            "task.checklistitem.add": {"TASKID": 1, "FIELDS": {"TITLE": "Check", "PARENT_ID": 0}},
            "task.checklistitem.update": {"TASKID": 1, "ITEMID": 2, "FIELDS": {"TITLE": "Changed"}},
            "task.checklistitem.complete": {"TASKID": 1, "ITEMID": 2},
            "task.checklistitem.renew": {"TASKID": 1, "ITEMID": 2},
            "task.elapseditem.add": {"TASKID": 1, "ARFIELDS": {"SECONDS": 60, "COMMENT_TEXT": "Work"}},
            "task.item.approve": {"TASKID": 1},
            "task.item.disapprove": {"TASKID": 1},
        }
        self.assertEqual(set(examples), set(self.modules.API_WRITE))
        for method, arguments in examples.items():
            with self.subTest(method=method):
                result = self.write.main("api", {"method": method, "arguments": arguments})
                self.assertEqual(result["outcome"], "planned")
                self.assertFalse(result["mutated"])
                self.assertEqual(result["method"], method)
        self.secret.assert_not_called()
        self.opener.open.assert_not_called()

    def test_portal_write_executes_once_and_does_not_replay_unknown_outcome(self):
        args = {"method": "im.message.add", "arguments": {"DIALOG_ID": "1", "MESSAGE": "Exact text"}}
        self.response({"result": 88})
        result = self.write.main("api", args, apply=True)
        self.assertEqual(result["receipt"], {"method": "im.message.add", "id": 88})
        sent = json.loads(self.opener.open.call_args.args[0].data)
        self.assertEqual(sent, {"DIALOG_ID": "1", "MESSAGE": "Exact text", "URL_PREVIEW": "N", "SYSTEM": "N"})
        self.opener.open.reset_mock()
        self.opener.open.side_effect = TimeoutError()
        result = self.write.main("api", args, apply=True)
        self.assertEqual(result["outcome"], "unknown")
        self.assertIsNone(result["mutated"])
        self.assertFalse(result["retry_safe"])
        self.opener.open.assert_called_once()
