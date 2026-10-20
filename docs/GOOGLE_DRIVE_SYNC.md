# Google DriveへGitHubリポジトリを同期する

この手動ワークフローは、選択したGitHubコミットの追跡対象ファイルをZIPにし、指定したGoogle Driveフォルダへアップロードします。Driveに置いたZIPは、Google Workspace / GeminiのDrive連携から参照できます。GitHubへのpushだけではDriveへ送られません。

認証はWorkload Identity Federation（WIF）を使います。GitHub ActionsがOIDCでGoogle Cloudへ認証するため、サービスアカウントJSONキーを発行・保存する必要はありません。サービスアカウント鍵の作成を禁止する組織ポリシーは、そのまま有効にしておけます。

## 初回設定

### Google Cloud

1. 専用のGoogle Cloudプロジェクトを選び、Google Drive API、Security Token Service API、IAM Service Account Credentials APIを有効にします。
2. **IAMと管理 → サービス アカウント**で `github-drive-sync` などの専用サービスアカウントを作ります。プロジェクトロールは不要です。JSON鍵は作成しません。
3. **IAMと管理 → Workload Identity Federation**でWorkload Identity Poolを作り、そのPoolにOpenID Connect（OIDC）プロバイダーを追加します。
   - Issuer URL: `https://token.actions.githubusercontent.com`
   - Attribute mapping: `google.subject` → `assertion.sub`、`attribute.repository` → `assertion.repository`、`attribute.ref` → `assertion.ref`
   - Attribute condition: `assertion.repository == 'nyu1791-collab/voiceBox-Pipeline' && assertion.ref == 'refs/heads/main'`
4. 作成したプロバイダーのリソース名を控えます。形式は `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL_ID/providers/PROVIDER_ID` です。プロジェクトIDではなく数値のプロジェクト番号を使います。
5. サービスアカウントの権限で、対象Workload Identity Poolのプリンシパルに **Workload Identity ユーザー（`roles/iam.workloadIdentityUser`）** を付与します。プリンシパルは次の形式です。

   `principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL_ID/attribute.repository/nyu1791-collab/voiceBox-Pipeline`

   ここで使うプロジェクト番号とPool IDは、手順4の値と同じです。
6. Google Driveに専用の共有ドライブと同期先フォルダを作り、共有ドライブのメンバーにサービスアカウントを **投稿者（Contributor）** として追加します。サービスアカウントには共有ドライブ全体への投稿権限が付くため、専用の共有ドライブを使ってください。フォルダIDを控えます（URLの `/folders/` の後ろの部分です）。

### GitHub

GitHubリポジトリの **Settings → Secrets and variables → Actions → Variables** で次のRepository variablesを登録します。これらは認証秘密ではなく、接続先を指定するIDです。

- `GOOGLE_WORKLOAD_IDENTITY_PROVIDER`: 手順4で控えたプロバイダーのリソース名
- `GOOGLE_SERVICE_ACCOUNT`: サービスアカウントのメールアドレス
- `GOOGLE_DRIVE_FOLDER_ID`: 同期先フォルダのID

## 同期の実行

GitHubリポジトリの **Actions → Sync repository to Google Drive → Run workflow** を選び、`main` ブランチから実行します。成功すると、Driveフォルダに `リポジトリ名-コミットSHA.zip` が作られます。新しい実行は新しいZIPを追加し、過去のスナップショットは残ります。

ワークフローは手動実行のみで、Driveへ送る内容はそのコミットでGit管理されているファイルです。未コミットのローカルファイルやGitHub ActionsのSecretsは含まれません。台本・原素材などをGitへ入れない運用は引き続き守ってください。

## 注意

- Google Driveの接続は、Gemini側でも同期先フォルダを読めるGoogleアカウントで行ってください。
- Googleサービスアカウントには個人の「マイドライブ」の保存容量がありません。同期先には共有ドライブを使ってください。共有ドライブを作成できない場合は、Workspace管理者に相談してください。
- Workspace管理者がWorkload Identity Poolやサービスアカウントの共有ドライブ参加を制限している場合は、管理者に組織のポリシーを確認してください。組織全体のサービスアカウント鍵作成ポリシーを無効にする必要はありません。
- 同期を無効にするには、このワークフローを削除するか、3つのGitHub Repository variablesを削除します。
