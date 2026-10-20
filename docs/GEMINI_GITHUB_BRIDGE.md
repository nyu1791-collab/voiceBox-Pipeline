# Gemini assisted GitHub changes

This workflow lets Gemini propose repository changes through the existing `GEMINI_API_KEY` Actions secret.

## Run it

1. Merge `.github/workflows/gemini-pr.yml` and `tools/gemini_pr.py` into `main`.
2. In GitHub, open **Actions → Gemini assisted pull request → Run workflow**.
3. Enter a concrete change request and run it on `main`.
4. Review the resulting draft pull request and its diff before merging.

## Access and review

- The workflow is manual and runs only from `main`.
- Its token gets `contents: write` to push a proposal branch and `pull-requests: write` to open a draft PR. Other workflows keep their existing read-only token permissions.
- The Gemini API key is exposed only to the patch-generation step. The key is not sent to Gemini as repository text and is not printed by the script.
- Gemini receives tracked text files from the repository, capped at 120,000 characters. Binary files and secret-like filenames are skipped; common Google, GitHub, and API key patterns are redacted.
- The workflow does not let the model change files under `.github/`, run generated code, run tests, or merge a PR. Review the generated diff before merging.
- This workflow connects GitHub Actions to the Gemini API. It does not grant the Gemini web app direct GitHub write access.
