"""Read-only authentication checks. Never call model generation or agent prompts."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_json(url, headers):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        return json.load(response)


def check(cli, output):
    report = {"status": "FAILED", "generation_requests": 0,
              "google_account_for_setup": "n.yu17919191@gmail.com",
              "project_id": os.environ.get("GCP_PROJECT_ID", ""),
              "service_account": os.environ.get("GCP_SERVICE_ACCOUNT", ""),
              "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
              "vertex": {"status": "FAILED"}, "antigravity": {"status": "FAILED"},
              "scope": "authentication and metadata only; model inference not tested"}
    try:
        token = os.environ.pop("VERTEX_ACCESS_TOKEN", "")
        project = report["project_id"]
        if not token or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
            raise ValueError("Missing Vertex setup")
        # Listing existing Model resources is read-only, has no inference cost,
        # and uses the actual Vertex AI User role without Service Usage admin access.
        data = read_json("https://us-central1-aiplatform.googleapis.com/v1/projects/" + project +
                         "/locations/us-central1/models?pageSize=1", {"Authorization": "Bearer " + token})
        del token
        if not isinstance(data, dict):
            raise ValueError("Unexpected Vertex metadata response")
        report["vertex"] = {"status": "SUCCESS", "metadata_access": True,
                            "metadata_region": "us-central1",
                            "authentication": "GitHub OIDC / WIF / dedicated service account"}
    except Exception as error:
        report["vertex"]["error_type"] = type(error).__name__
        if isinstance(error, urllib.error.HTTPError):
            report["vertex"]["http_status"] = error.code
    try:
        key = os.environ.pop("GEMINI_API_KEY", "")
        if not key:
            raise ValueError("Missing Antigravity API key")
        data = read_json("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                         {"x-goog-api-key": key})
        del key
        if not data.get("models"):
            raise ValueError("No Gemini model metadata returned")
        settings = json.loads((Path.home() / ".gemini/antigravity-cli/settings.json").read_text())
        if settings.get("modelProvider") != "gemini":
            raise ValueError("Antigravity provider is not configured")
        result = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=30)
        if result.returncode or result.stdout.strip() != "1.4.3":
            raise ValueError("Unexpected CLI version")
        report["antigravity"] = {"status": "SUCCESS", "cli_version": "1.4.3",
            "model_provider": "gemini", "api_key_metadata_access": True,
            "secret_name": "ANTIGRAVITY_GEMINI_API_KEY", "agent_prompt_tested": False}
    except Exception as error:
        report["antigravity"]["error_type"] = type(error).__name__
        if isinstance(error, urllib.error.HTTPError):
            report["antigravity"]["http_status"] = error.code
    if all(report[name]["status"] == "SUCCESS" for name in ("vertex", "antigravity")):
        report["status"] = "SUCCESS"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("Vertex: " + report["vertex"]["status"] + "; Antigravity setup: " + report["antigravity"]["status"])
    return int(report["status"] != "SUCCESS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(check(args.cli, args.output))
