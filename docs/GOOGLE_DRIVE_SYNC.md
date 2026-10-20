# Google DriveへGitHubリポジトリを同期する

この手動ワークフローは、選択したGitHubコミットの追跡対象ファイルをZIPにし、指定したGoogle Driveフォルダへアップロードします。Driveに置いたZIPは、Google Workspace / GeminiのDrive連携から参照できます。GitHubへのpushだけではDriveへ送られません。

## 初回設定

1. Google Cloudで専用のプロジェクトとサービスアカウントを作成し、Google Drive APIを有効にします。
2. サービスアカウントのJSONキーを作成します。キーはダウンロード後、安全な場所に保管してください。
3. Google Driveの共有ドライブに同期先フォルダを作り、共有ドライブのメンバーにサービスアカウントを **投稿者（Contributor）** として追加します。サービスアカウントには共有ドライブ全体への投稿権限が付くため、専用の共有ドライブを使ってください。フォルダIDを控えます（URLの `/folders/` の後ろの部分です）。
4. GitHubリポジトリの **Settings → Secrets and variables → Actions → New repository secret** で次のシークレットを登録します。

   - `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON`: JSONキーのファイル内容全体
   - `GOOGLE_DRIVE_FOLDER_ID`: 同期先フォルダのID

   JSONキーをコミットしたり、ログへ貼り付けたりしないでください。

## 同期の実行

GitHubリポジトリの **Actions → Sync repository to Google Drive → Run workflow** を選び、同期したいブランチから実行します。成功すると、Driveフォルダに `リポジトリ名-コミットSHA.zip` が作られます。新しい実行は新しいZIPを追加し、過去のスナップショットは残ります。

ワークフローは手動実行のみで、Driveへ送る内容はそのコミットでGit管理されているファイルです。未コミットのローカルファイルやGitHub Actionsのシークレットは含まれません。台本・原素材などをGitへ入れない運用は引き続き守ってください。

## 注意

- Google Driveの接続は、Gemini側でも同期先フォルダを読めるGoogleアカウントで行ってください。
- Googleサービスアカウントには個人の「マイドライブ」の保存容量がありません。同期先には共有ドライブを使ってください。共有ドライブを作成できない場合は、Workspace管理者に相談してください。
- Workspace管理者がサービスアカウントのメンバー追加を制限している場合も、管理者に組織の共有ポリシーを確認してください。
- 同期を無効にするには、このワークフローを削除するか、GitHub Secretsから2つの値を削除します。
