# トレンドから一次資料・撮影・DeepSeek台本へ

`Research and JSON Project` の `mode: evidence_first` は、従来の `standard` と別の準備経路。

1. `queries` の短い検索語・RSSから話題を発見する。説明文の `topic` と検索語を分ける。
2. 発見ページ内のリンクと検索結果から、指定した `official_hosts` に完全一致するHTTPSページを取得する。ホストの指定は担当が公式配布元と確認して行う。ドメイン一致だけで個々の主張の正しさを保証しない。
3. 抽出された記事日付が直近 `days` 日（UTC、当日を含む）に収まる本文だけを残す。日付不明・期間外・公式外へのリダイレクトは除外理由を保存する。抽出日付はサイトのメタデータ等によるもので、人による公表日の確認と同一ではない。
4. 最大4記事、各3段落までを実ブラウザーで撮影する。原画面、引用文、撮影UTC、SHAを保存する。撮影が一切成功しなければLLMを呼ばない。
5. 撮影済み段落だけを無料DeepSeekへ1回渡し、台詞の `source_id`・`evidence_id` と段落全文の日本語訳を求める。未撮影のID、別出典のID、訳文欠落は失敗として停止する。
6. `project.json` と読みやすい `script.md` を保存する。`PROJECT_READY` は音声・動画完成を意味しない。

`provider:none` では撮影とプロンプトの準備まで行い、`EVIDENCE_READY` を返す。従来経路の `RESEARCH_READY` と区別する。

ローカル入口：

```sh
python tools/prepare_trending.py --request request.json --output 新しいフォルダ --allow-api
```

入力例（素材・台本は公開Gitに保存しない）：

```json
{
  "topic": "最近のAI公式発表をずんだもんと四国めたんが解説",
  "queries": ["site:openai.com/index/ October 2026", "site:anthropic.com/news October 2026"],
  "official_hosts": ["openai.com", "anthropic.com", "www.anthropic.com"],
  "days": 7,
  "max_sources": 20,
  "minutes": 3,
  "provider": "opencode",
  "model": "deepseek-v4.1-flash-free"
}
```

検索結果や発見ページ内リンクを最大20候補まで追う限定的な探索で、Google News集約URLを強制解決する処理ではない。サイトのアクセス制限は回避しない。段落選択は本文中の長さ80〜600文字の先頭3段落までで、話題の網羅性や原画像の360pでの可読性は保証しない。複数の記事が同じ発表を扱う場合の意味的な重複除去も未実装（本文SHA一致のみ重複除去）。日本語訳はLLMの生成物であり、ID照合は訳の意味的正確さの保証ではない。今回の実装には動画制作・投稿・自動公開を含めない。
