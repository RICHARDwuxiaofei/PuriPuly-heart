from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from puripuly_heart.config.paths import default_settings_path, test_profile_name, user_config_dir
from puripuly_heart.qq_auth_beta import CHANNEL, PROFILE, configure_channel

PRODUCTION_BROKER = "https://puripuly-heart-broker.kapitalismho.workers.dev"


def _local(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.port is not None
        and parsed.path in {"", "/"}
        and not parsed.username
    )


def _build():
    from puripuly_heart import __version__

    for candidate in (
        Path(__file__).resolve().parents[4] / "BUILD.json",
        Path(sys.executable).resolve().parents[1] / "BUILD.json",
    ):
        if candidate.is_file():
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            return {
                "beta_version": raw.get("version", __version__),
                "source_sha": raw.get("commit", "unknown"),
            }
    return {"beta_version": __version__, "source_sha": "source-checkout"}


def inspect_credentials():
    from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner
    from puripuly_heart.app.wiring.wiring_managed_auth_factory import (
        build_openrouter_credential_runtime_config_from_vnext,
    )
    from puripuly_heart.app.wiring.wiring_secrets_factory import create_secret_store
    from puripuly_heart.core.openrouter.openrouter_credentials import resolve_openrouter_credentials
    from puripuly_heart.core.qq_delivery_receipt import (
        QQ_DELIVERY_RECEIPT_SECRET,
        decode_receipt,
        receipt_matches,
    )

    report = dict(
        tool_version=1,
        channel=CHANNEL,
        profile=test_profile_name(),
        **_build(),
        has_key=False,
        key_saved=False,
        pending_operation=False,
        pending_ack=False,
        ack="not_verified",
        runtime="not_tested",
        usage="not_requested",
        authenticated=False,
        online_account="NOT_VERIFIED",
    )
    path = default_settings_path()
    if not path.is_file():
        report["settings"] = "absent"
        return report
    owner = compose_settings_owner(path)
    settings = owner.persistence.load_active(path).canonical_settings
    state = settings.state.managed_connection
    report.update(
        pending_operation=bool(state.pending_managed_operation_id),
        pending_ack=bool(state.pending_delivery_ack_source),
        operation_state=state.pending_managed_operation_state,
        settings="present",
        selected_connection=settings.intent.translation.connection,
    )
    try:
        secrets = create_secret_store(settings.intent.secrets, config_path=path)
        key = secrets.get("openrouter_managed_qq_api_key")
        receipt = decode_receipt(secrets.get(QQ_DELIVERY_RECEIPT_SECRET))
        report.update(has_key=bool(key), key_saved=bool(key), receipt_present=bool(receipt))
        bound = bool(
            receipt
            and receipt_matches(
                receipt,
                key=key,
                installation=state.installation_id,
                reference=state.active_managed_credential_ref,
            )
        )
        report["ack"] = (
            "confirmed_locally" if bound and receipt["acknowledged"] else "pending_or_unbound"
        )
        resolution = resolve_openrouter_credentials(
            build_openrouter_credential_runtime_config_from_vnext(settings), secrets=secrets
        )
        report["credential_consistent"] = bool(
            bound
            and resolution.api_key
            and not report["pending_operation"]
            and not report["pending_ack"]
        )
        report["authenticated"] = report["credential_consistent"]
    except Exception as exc:
        from puripuly_heart.core.qq_auth_diagnostics import exception_fields

        report.update(error="local_credential_unavailable", **exception_fields(exc))
    from puripuly_heart.core.external_network import ProxyPolicy, select_tls

    report["proxy_source"] = (
        ProxyPolicy().route(settings.intent.translation.openrouter_broker_base_url).source
    )
    tls = select_tls()
    report.update(tls_source=tls.source, tls_backend=tls.backend)
    return report


async def refresh_usage_readonly(report):
    if not report.get("credential_consistent"):
        report["usage"] = "credential_unconfirmed"
        return
    from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner
    from puripuly_heart.app.wiring.wiring_secrets_factory import create_secret_store
    from puripuly_heart.core.qq_auth_diagnostics import exception_fields
    from puripuly_heart.core.qq_auth_trace import auth_trace, trace_stage
    from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider

    owner = compose_settings_owner(default_settings_path())
    settings = owner.persistence.load_active(owner.path).canonical_settings
    store = create_secret_store(settings.intent.secrets, config_path=owner.path)
    try:
        with auth_trace(), trace_stage("usage_refresh"):
            result = await OpenRouterLLMProvider.fetch_key_metadata(
                store.get("openrouter_managed_qq_api_key"), strict=True
            )
            report["usage"] = "ready" if result is not None else "response_invalid"
    except Exception as exc:
        report["usage"] = "request_failed"
        report["usage_error"] = exception_fields(exc)
        status = getattr(exc, "http_status", None)
        if type(status) is int and 100 <= status <= 599:
            report["usage_http_status"] = status


async def app_attempt(
    identity, credential, *, local_broker=None, translate=False, recover_only=False
):
    from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner
    from puripuly_heart.app.wiring.wiring_managed_account import ManagedOpenRouterReleaseRuntime
    from puripuly_heart.app.wiring.wiring_managed_auth_factory import ManagedAuthRuntimeAdapter
    from puripuly_heart.app.wiring.wiring_secrets_factory import create_secret_store
    from puripuly_heart.core.qq_auth_trace import auth_trace, trace_stage

    owner = compose_settings_owner(default_settings_path())
    settings = owner.start().settings
    if (
        not local_broker
        and settings.intent.translation.openrouter_broker_base_url.rstrip("/") != PRODUCTION_BROKER
    ):
        raise ValueError("Online probe target does not match the allowed production broker")
    if local_broker:
        if not _local(local_broker):
            raise ValueError("Synthetic credentials require a loopback broker")
        settings = replace(
            settings,
            intent=replace(
                settings.intent,
                translation=replace(
                    settings.intent.translation,
                    connection="managed_china",
                    model="deepseek_v4_flash_41",
                    openrouter_selected_source="managed",
                    openrouter_broker_base_url=local_broker,
                ),
            ),
        )
        owner.canonical = settings
        owner.persist()
    if recover_only and not (
        settings.state.managed_connection.pending_managed_operation_id
        or settings.state.managed_connection.pending_delivery_ack_source
    ):
        return inspect_credentials()
    secrets = create_secret_store(settings.intent.secrets, config_path=owner.path)
    release = ManagedOpenRouterReleaseRuntime(
        settings=owner, config_path=owner.path, callback_received=lambda: None
    )
    service = await release.rebuild(secrets=secrets)
    adapter = ManagedAuthRuntimeAdapter(
        config_path=owner.path,
        settings=owner,
        secret_store_factory=create_secret_store,
        release_service_provider=lambda: release.service,
        settings_repository_factory=lambda *args: None,
        runtime_presence_provider=lambda: (True, False),
        ingress_provider=lambda: False,
    )
    provider = None
    try:
        with auth_trace():
            result = await adapter.execute_qq(identity, credential, None)
            report = inspect_credentials()
            report["authentication_result"] = (
                "committed"
                if result.succeeded
                else "pending" if result.delivery_ack_pending else "failed"
            )
            report["authenticated"] = bool(result.succeeded and report.get("credential_consistent"))
            diagnostics = (
                result.transaction_result.diagnostics if result.transaction_result else None
            )
            if diagnostics:
                from puripuly_heart.core.qq_auth_diagnostics import CODES, safe_fields

                report["error_code"] = (
                    diagnostics.code if diagnostics.code in CODES else "unknown_error"
                )
                report.update(safe_fields(diagnostics.fields))
            if result.succeeded:
                from puripuly_heart.app.wiring.wiring_llm_factory import (
                    create_llm_provider,
                    llm_factory_extras_from_vnext,
                    runtime_resolution_input_from_vnext,
                )

                try:
                    with trace_stage("provider_apply"):
                        fresh = create_secret_store(
                            owner.canonical.intent.secrets, config_path=owner.path
                        )
                        provider = create_llm_provider(
                            runtime_resolution_input_from_vnext(owner.canonical),
                            secrets=fresh,
                            extras=llm_factory_extras_from_vnext(owner.canonical),
                            managed_release_service=service,
                        )
                        report["runtime"] = "provider_created"
                except Exception as exc:
                    from puripuly_heart.core.qq_auth_diagnostics import exception_fields

                    report["runtime"] = "provider_apply_failed"
                    report["runtime_error"] = exception_fields(exc)
                    return report
                if translate:
                    if not local_broker or not _local(local_broker):
                        raise ValueError("Automatic translation is limited to loopback tests")
                    from puripuly_heart.providers.llm.openrouter import OpenRouterLLMProvider

                    primary = provider
                    for _ in range(5):
                        primary = getattr(primary, "inner", getattr(primary, "primary", primary))
                    if not isinstance(primary, OpenRouterLLMProvider):
                        raise ValueError("Local test did not create the expected real provider")
                    primary.base_url = local_broker + "/api/v1"
                    response = await primary.translate(
                        utterance_id=uuid4(),
                        text="local synthetic test",
                        system_prompt="Translate to Chinese",
                        source_language="en",
                        target_language="zh",
                    )
                    report["runtime"] = "translation_received" if response else "translation_empty"
            trace_path = user_config_dir() / "logs" / "qq-auth-stages.jsonl"
            if trace_path.is_file():
                from puripuly_heart.core.qq_auth_diagnostics import safe_fields

                allowed = {
                    "correlation",
                    "profile",
                    "stage",
                    "outcome",
                    "elapsed_ms",
                    "since_start_ms",
                    "code",
                }
                events = [
                    json.loads(line)
                    for line in trace_path.read_text(encoding="utf-8").splitlines()[-200:]
                ]
                report["stages"] = [
                    {**{key: item[key] for key in allowed if key in item}, **safe_fields(item)}
                    for item in events
                ]
            return report
    finally:
        if provider is not None:
            await provider.close()
        await release.close()


class ControlledBroker:
    def __init__(self, case="normal"):
        self.case = case
        self.requests = []
        self.operation = None
        self.ack_count = 0
        self.translation_received = False
        self.assert_count = 0
        self.ack_entered = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                owner.requests.append({"path": self.path, "fields": sorted(body)})
                code = 200
                if self.path == "/v1/auth/qq/assert":
                    owner.assert_count += 1
                    owner.operation = body.get("operation_id")
                    result = owner.delivery()
                    if owner.case == "business-false":
                        result["ok"] = False
                        owner.operation = None
                elif self.path.endswith("/managed-operation/status") or self.path.endswith(
                    "/managed-operation/resume"
                ):
                    if body.get("operation_id") != owner.operation:
                        code, result = 404, {}
                    elif owner.case == "terminal":
                        result = {
                            "ok": True,
                            "state": "FAILED",
                            "client_action": "action_required",
                            "failure_reason": "terminal_provider_failure",
                        }
                    elif owner.ack_count:
                        result = {
                            "ok": True,
                            "state": "ACTIVE",
                            "client_action": "wait",
                            "managed_credential_ref": "local-reference",
                        }
                    else:
                        result = owner.delivery()
                elif self.path.endswith("/managed-key-delivery/ack"):
                    owner.ack_entered.set()
                    if owner.case == "exit-before-ack":
                        time.sleep(10)
                    if body != {
                        "delivery_id": "local-delivery",
                        "managed_credential_ref": "local-reference",
                        "delivery_ack_token": "synthetic-local-ack",
                    }:
                        code, result = 400, {"ok": False, "status": "invalid_token"}
                    elif owner.case == "ack-false":
                        result = {"ok": False, "status": "acknowledged"}
                    else:
                        owner.ack_count += 1
                        result = {
                            "ok": True,
                            "status": (
                                "acknowledged" if owner.ack_count == 1 else "already_acknowledged"
                            ),
                        }
                elif self.path == "/api/v1/chat/completions":
                    owner.translation_received = (
                        self.headers.get("Authorization") == "Bearer synthetic-local-key"
                    )
                    result = {
                        "choices": [{"message": {"content": "本地测试"}, "finish_reason": "stop"}],
                        "usage": {},
                    }
                else:
                    code, result = 404, {"ok": False}
                data = json.dumps(result).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except BrokenPipeError, ConnectionResetError, ConnectionAbortedError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def delivery(self):
        return dict(
            ok=True,
            status="delivery_pending",
            openrouter_api_key="synthetic-local-key",
            qq_subject_ref="local-subject",
            managed_credential_ref="local-reference",
            delivery_ack_required=True,
            delivery_id="local-delivery",
            delivery_ack_token="synthetic-local-ack",
            delivery_ack_expires_at="2099-01-01T00:00:00.000Z",
            issue_source="qq",
        )

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


def offline_selftest(output):
    results = []
    with tempfile.TemporaryDirectory(prefix="qq-auth-offline-") as temporary:
        for case in ("normal", "normal-keyring", "ack-false", "business-false", "exit-before-ack"):
            data = Path(temporary) / case
            data.mkdir()
            environment = dict(
                os.environ,
                PURIPULY_HEART_TEST_PROFILE="qq-auth-beta-offline-" + uuid4().hex[:12],
                PURIPULY_HEART_TEST_DATA_DIR=str(data),
                NO_PROXY="127.0.0.1,localhost",
                no_proxy="127.0.0.1,localhost",
                PURIPULY_HEART_SECRETS_PASSPHRASE=uuid4().hex,
            )
            seed = "from dataclasses import replace; from puripuly_heart.app.services.canonical_settings_persistence import compose_settings_owner; from puripuly_heart.config.paths import default_settings_path; o=compose_settings_owner(default_settings_path()); s=o.start().settings; o.canonical=replace(s,intent=replace(s.intent,secrets=replace(s.intent.secrets,backend='encrypted_file'))); o.persist()"
            if case == "normal-keyring":
                seed = seed.replace("backend='encrypted_file'", "backend='keyring'")
            subprocess.run(
                [sys.executable, "-c", seed], env=environment, check=True, capture_output=True
            )
            with ControlledBroker(case) as broker:
                command = [
                    sys.executable,
                    "-m",
                    __name__,
                    "--mode",
                    "local",
                    "--broker",
                    broker.url,
                    "--output",
                    str(data / "result.json"),
                ]
                if case == "exit-before-ack":
                    child = subprocess.Popen(
                        command,
                        env=environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if not broker.ack_entered.wait(20):
                        child.kill()
                        raise RuntimeError("Child did not reach the ACK boundary")
                    child.kill()
                    child.wait()
                    broker.case = "normal"
                completed = subprocess.run(
                    command, env=environment, capture_output=True, timeout=40
                )
                if case in {"normal", "normal-keyring"} and completed.returncode == 0:
                    first_report = json.loads((data / "result.json").read_text(encoding="utf-8"))
                    if first_report.get("authentication_result") != "committed":
                        raise RuntimeError("Initial authentication did not commit")
                    broker.translation_received = False
                    completed = subprocess.run(
                        command, env=environment, capture_output=True, timeout=40
                    )
                report = (
                    json.loads((data / "result.json").read_text(encoding="utf-8"))
                    if (data / "result.json").exists()
                    else {"error": "child_failed"}
                )
                expected = case in {"normal", "normal-keyring", "exit-before-ack"}
                passed = (
                    completed.returncode == 0
                    and report.get("authenticated") is expected
                    and (not expected or broker.translation_received)
                    and (not expected or report.get("authentication_result") == "committed")
                    and (not expected or report.get("runtime") == "translation_received")
                    and broker.assert_count == 1
                )
                results.append(
                    dict(
                        case=case,
                        passed=passed,
                        assertions=broker.assert_count,
                        ack_requests=broker.ack_count,
                        correct_translation_credential=broker.translation_received,
                        requests=broker.requests,
                        result=report,
                    )
                )
            if case == "normal-keyring":
                import keyring

                from puripuly_heart.core.managed_identity import (
                    MANAGED_DEVICE_PRIVATE_KEY_SECRET,
                    MANAGED_DEVICE_PUBLIC_KEY_SECRET,
                    MANAGED_IDENTITY_BINDING_SECRET,
                )
                from puripuly_heart.core.qq_delivery_receipt import QQ_DELIVERY_RECEIPT_SECRET

                for key in (
                    MANAGED_DEVICE_PRIVATE_KEY_SECRET,
                    MANAGED_DEVICE_PUBLIC_KEY_SECRET,
                    MANAGED_IDENTITY_BINDING_SECRET,
                    QQ_DELIVERY_RECEIPT_SECRET,
                    "openrouter_managed_qq_api_key",
                    "openrouter_managed_qq_status_auth",
                    "openrouter_managed_operation_resume_token",
                    "openrouter_managed_qq_delivery_ack_token",
                ):
                    try:
                        keyring.delete_password(
                            "puripuly-heart.test." + environment["PURIPULY_HEART_TEST_PROFILE"], key
                        )
                    except keyring.errors.PasswordDeleteError:
                        pass
        with ControlledBroker() as broker:
            minimal = dict(
                qq_identity="100001",
                credential="a" * 64,
                asserted_at=datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                delivery_ack_supported=True,
            )
            request = urllib.request.Request(
                broker.url + "/v1/auth/qq/assert",
                json.dumps(minimal).encode(),
                headers={
                    "User-Agent": "PuriPulyDesktop/2.6.1",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
            ack = {
                key: payload[key]
                for key in ("delivery_id", "managed_credential_ref", "delivery_ack_token")
            }
            with urllib.request.urlopen(
                urllib.request.Request(
                    broker.url + "/v1/providers/openrouter/managed-key-delivery/ack",
                    json.dumps(ack).encode(),
                    headers={"Content-Type": "application/json"},
                ),
                timeout=15,
            ) as response:
                ack_result = json.load(response)
            results.append(
                dict(
                    case="minimal-python-reference-fresh-state",
                    passed=bool(
                        payload.get("openrouter_api_key")
                        and ack_result.get("ok") is True
                        and ack_result.get("status") == "acknowledged"
                    ),
                    requests=broker.requests,
                )
            )
    return dict(
        tool_version=1,
        channel=CHANNEL,
        **_build(),
        test_type="real-loopback-http-with-real-app-auth-wiring-storage-and-provider",
        production_requests=0,
        results=results,
        passed=all(item["passed"] for item in results),
    )


def write_report(report, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    destination.with_suffix(".txt").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="QQ Auth Beta 脱敏诊断。默认仅离线测试，不向生产服务申请密钥。"
    )
    parser.add_argument(
        "--mode", choices=("offline", "inspect", "app", "recover", "local"), default="offline"
    )
    parser.add_argument("--broker", default=PRODUCTION_BROKER)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--refresh-usage",
        action="store_true",
        help="仅配合 inspect：明确执行一次只读用量查询，不申请密钥",
    )
    args = parser.parse_args(argv)
    lease = None
    if args.mode != "local":
        configure_channel()
    logging.basicConfig(level=logging.WARNING)
    destination = args.output or user_config_dir() / "diagnostics" / (
        "qq-auth-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
    )
    try:
        if args.mode == "offline":
            report = offline_selftest(destination)
        elif args.mode == "inspect":
            report = inspect_credentials()
            if args.refresh_usage:
                asyncio.run(refresh_usage_readonly(report))
        elif args.mode == "local":
            if not _local(args.broker) or not (test_profile_name() or "").startswith(
                "qq-auth-beta-offline-"
            ):
                raise ValueError(
                    "Local synthetic mode requires a dedicated offline profile and loopback target"
                )
            report = asyncio.run(
                app_attempt("100001", "a" * 64, local_broker=args.broker, translate=True)
            )
        else:
            if (
                test_profile_name() != PROFILE
                or args.broker != PRODUCTION_BROKER
                or not sys.stdin.isatty()
            ):
                raise ValueError("联网诊断仅允许在新 Beta profile 中由用户本机交互确认")
            initial = inspect_credentials()
            if (
                args.mode == "recover"
                and not initial.get("pending_operation")
                and not initial.get("pending_ack")
            ):
                report = initial
            else:
                print(
                    "目标：QQ Auth Beta 专属配置。不会打印 API Key。请求结果不确定时保留原操作，不自动重新领取。"
                )
                if args.mode == "app":
                    identity = input("输入合法的 QQ 数字标识：").strip()
                    credential = getpass.getpass(
                        "粘贴现成的 64 位小写十六进制激活凭证（隐藏输入）："
                    ).strip()
                    if (
                        not identity.isascii()
                        or not identity.isdecimal()
                        or not 5 <= len(identity) <= 20
                        or len(credential) != 64
                        or any(ch not in "0123456789abcdef" for ch in credential)
                    ):
                        raise ValueError("标识或凭证格式不正确")
                else:
                    identity, credential = "", ""
                if input("本次仅尝试一次。确认请完整输入 QQ AUTH BETA：") != "QQ AUTH BETA":
                    report = {**initial, "action": "cancelled"}
                else:
                    from puripuly_heart.core.control_instance import acquire

                    lease = acquire(default_settings_path())
                    report = asyncio.run(
                        app_attempt(identity, credential, recover_only=args.mode == "recover")
                    )
        write_report(report, destination)
        print("已保存脱敏诊断：" + str(destination))
        if report.get("passed") is False:
            return 1
        return 0
    except Exception as exc:
        from puripuly_heart.core.qq_auth_diagnostics import exception_fields

        write_report(
            dict(channel=CHANNEL, **_build(), error="diagnostic_failed", **exception_fields(exc)),
            destination,
        )
        print("诊断未完成；请回传生成的脱敏 JSON，不要回传设置或密钥库。")
        return 1
    finally:
        if lease is not None:
            lease.close()


if __name__ == "__main__":
    raise SystemExit(main())
