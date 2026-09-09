// ============================================================
// Roast Studio - macOS ネイティブアプリ (薄いラッパー)
// Copyright (c) 2026 Ossan's Coffee / Tomi
// Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
// ------------------------------------------------------------
// 既存のWeb版(app/server.py + app/static/index.html)を
// そのまま同梱し、起動時にPythonサーバーを立ち上げてから
// WKWebViewでlocalhost:8765を表示するだけのアプリ。
//
// バックエンド・GUIともにWeb版(app/, roastlib/)と完全共有。
// このファイルはウィンドウ生成とサーバープロセスの起動/停止のみを担当する。
// ============================================================

import AppKit
import Foundation
import UserNotifications
import WebKit

let serverPort = 8765

/// 起動時の診断ログをファイルに書き出す。NSLogはこのアプリ(アドホック署名)では
/// `log show`に一切現れないことが判明したため、確実に確認できるファイル書き込みを使う。
let debugLogPath = "/tmp/roast_macapp_debug.log"
func debugLog(_ message: String) {
    let line = "\(Date()) \(message)\n"
    guard let data = line.data(using: .utf8) else { return }
    if FileManager.default.fileExists(atPath: debugLogPath) {
        if let handle = FileHandle(forWritingAtPath: debugLogPath) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        }
    } else {
        try? data.write(to: URL(fileURLWithPath: debugLogPath))
    }
}

/// WKWebViewが読み込むURL。起動フローの判定結果で決まる:
///   - Tailscaleが使える場合: https://<MagicDNS名>:8765(Web Push通知が使えるHTTPS版)
///   - 使えない場合: http://127.0.0.1:8765(通知機能のない従来版)
var appURL = URL(string: "http://127.0.0.1:\(serverPort)")!

// ============================================================
// シェルコマンド実行(タイムアウト付き)
// ============================================================
@discardableResult
func runCommand(
    _ executable: String, _ args: [String],
    cwd: String? = nil, timeout: TimeInterval = 15
) -> (status: Int32, output: String) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = args
    if let cwd = cwd { process.currentDirectoryURL = URL(fileURLWithPath: cwd) }
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    var buffer = Data()
    let bufferLock = NSLock()
    pipe.fileHandleForReading.readabilityHandler = { handle in
        let data = handle.availableData
        if !data.isEmpty {
            bufferLock.lock(); buffer.append(data); bufferLock.unlock()
        }
    }
    do {
        try process.run()
    } catch {
        pipe.fileHandleForReading.readabilityHandler = nil
        return (-1, "\(error)")
    }
    let deadline = Date().addingTimeInterval(timeout)
    while process.isRunning && Date() < deadline {
        Thread.sleep(forTimeInterval: 0.1)
    }
    if process.isRunning {
        process.terminate()
        Thread.sleep(forTimeInterval: 0.3)
    }
    pipe.fileHandleForReading.readabilityHandler = nil
    if let rest = ((try? pipe.fileHandleForReading.readToEnd()) ?? nil), !rest.isEmpty {
        bufferLock.lock(); buffer.append(rest); bufferLock.unlock()
    }
    bufferLock.lock()
    let output = String(data: buffer, encoding: .utf8) ?? ""
    bufferLock.unlock()
    return (process.terminationStatus, output)
}

// ============================================================
// Tailscale連携
//   Web Push通知(画面ロック中のスマホにも届く通知)にはHTTPSが必須で、
//   このアプリではTailscaleの証明書機能(tailscale cert)を使っている
//   (詳細はstart_app_https.commandを参照)。アプリ起動時に:
//     - CLIが見つかり、起動していなければ起動を試みる
//     - MagicDNS名を取得し、証明書を発行(更新)してHTTPSで起動
//     - どこかで失敗したら、通知機能のない従来版(HTTP)にフォールバック
// ============================================================
// /Applications/Tailscale.app/Contents/MacOS/Tailscale(アプリ本体のバイナリ)を
// アドホック署名アプリから直接Process()で叩くと、既に起動中のTailscale GUIと
// 正しく通信できず「The Tailscale GUI failed to start(CLIError error 3)」で
// 失敗することを確認済み(start_app_https.commandが使う専用CLIラッパーの方は
// 問題が起きないため、こちらを優先する)。
let tailscaleCLICandidates = [
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
]

func findTailscaleCLI() -> String? {
    let fm = FileManager.default
    return tailscaleCLICandidates.first { fm.isExecutableFile(atPath: $0) }
}

/// `tailscale status --json`のBackendState("Running"/"Stopped"/"NeedsLogin"等)。
/// デーモンに接続できない(未起動)場合はnil。
func tailscaleStatusJSON(_ cli: String) -> [String: Any]? {
    let (status, out) = runCommand(cli, ["status", "--json"], timeout: 8)
    guard let start = out.firstIndex(of: "{"),
          let data = String(out[start...]).data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        debugLog("tailscaleStatusJSON解析失敗 status=\(status) out=\(out.prefix(500))")
        return nil
    }
    return obj
}

func tailscaleBackendState(_ cli: String) -> String? {
    return tailscaleStatusJSON(cli)?["BackendState"] as? String
}

func tailscaleDNSName(_ cli: String) -> String? {
    guard let selfInfo = tailscaleStatusJSON(cli)?["Self"] as? [String: Any],
          let dns = selfInfo["DNSName"] as? String, !dns.isEmpty else { return nil }
    return dns.hasSuffix(".") ? String(dns.dropLast()) : dns
}

/// Tailscaleが動いていなければ起動を試み、"Running"になるまで待つ(最大20秒)。
/// GUIアプリ(メニューバー常駐)があればそれを起動し、ログイン済みで単に
/// 停止しているだけなら`tailscale up`で接続する。ログインが必要な状態
/// (NeedsLogin等)はユーザー操作なしには解決できないため、falseを返す。
func startTailscaleAndWait(_ cli: String) -> Bool {
    if tailscaleBackendState(cli) == "Running" { return true }
    if FileManager.default.fileExists(atPath: "/Applications/Tailscale.app") {
        _ = runCommand("/usr/bin/open", ["-g", "-a", "Tailscale"], timeout: 10)
    }
    var triedUp = false
    for _ in 0..<20 {
        if let state = tailscaleBackendState(cli) {
            switch state {
            case "Running":
                return true
            case "Stopped":
                if !triedUp {
                    triedUp = true
                    _ = runCommand(cli, ["up"], timeout: 12)
                }
            case "NeedsLogin", "NeedsMachineAuth":
                NSLog("[Tailscale] 状態=%@: ログイン操作が必要なため自動起動できません", state)
                return false
            default:
                break  // Starting等は待つ
            }
        }
        Thread.sleep(forTimeInterval: 1.0)
    }
    return tailscaleBackendState(cli) == "Running"
}

/// HTTPS証明書を発行(既にあれば更新)する。start_app_https.commandと同じく
/// certs/ts.crt・certs/ts.keyに出力する(certs/は.gitignore済み)。
func issueTailscaleCert(_ cli: String, hostname: String, repoRoot: String) -> Bool {
    try? FileManager.default.createDirectory(
        atPath: repoRoot + "/certs", withIntermediateDirectories: true)
    let (status, out) = runCommand(
        cli,
        ["cert", "--cert-file", "certs/ts.crt", "--key-file", "certs/ts.key", hostname],
        cwd: repoRoot, timeout: 60
    )
    if status != 0 {
        NSLog("[Tailscale] 証明書の発行に失敗: %@", out)
    }
    return status == 0
}

// ============================================================
// ポート8765の後始末
//   前回のアプリが完全に終了しておらず、古いサーバープロセスがポートを
//   掴んだままだと新しいサーバーが起動できない。このアプリのサーバー
//   (uvicorn/Python)と分かるプロセスだけを安全に終了させる。
// ============================================================
func pidsListeningOnPort(_ port: Int) -> [(pid: Int32, command: String)] {
    let (_, out) = runCommand(
        "/usr/sbin/lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-t"], timeout: 8)
    return out.split(separator: "\n").compactMap { line in
        guard let pid = Int32(line.trimmingCharacters(in: .whitespaces)) else { return nil }
        let (_, cmd) = runCommand("/bin/ps", ["-p", "\(pid)", "-o", "command="], timeout: 5)
        return (pid, cmd.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

/// ポートを掴んでいる古いサーバーを終了させる。成功(または何もいない)なら
/// nil、自動では解決できない場合はユーザー向けのエラーメッセージを返す。
func cleanupStaleServer(port: Int) -> String? {
    var entries = pidsListeningOnPort(port)
    if entries.isEmpty { return nil }

    func isOurServer(_ cmd: String) -> Bool {
        return cmd.contains("uvicorn") || cmd.contains("app.server")
            || cmd.lowercased().contains("python")
    }
    let foreign = entries.filter { !isOurServer($0.command) }
    if !foreign.isEmpty {
        let list = foreign.map { "  \($0.command)(PID \($0.pid))" }.joined(separator: "\n")
        return "ポート\(port)を別のアプリが使用しているため起動できません:\n\(list)\n\nそのアプリを終了してから、もう一度起動してください。"
    }

    NSLog("[起動] 前回のサーバープロセスが残っていたため終了します: %@",
          entries.map { "\($0.pid)" }.joined(separator: ", "))
    for e in entries { kill(e.pid, SIGTERM) }
    for _ in 0..<10 {
        Thread.sleep(forTimeInterval: 0.5)
        entries = pidsListeningOnPort(port)
        if entries.isEmpty { return nil }
    }
    // 5秒待っても残っている場合は強制終了
    for e in entries { kill(e.pid, SIGKILL) }
    Thread.sleep(forTimeInterval: 0.5)
    return pidsListeningOnPort(port).isEmpty
        ? nil
        : "ポート\(port)を解放できませんでした。Macを再起動してから、もう一度お試しください。"
}

/// UNUserNotificationCenterは、正式なバンドル(Info.plist・バンドルID)を持たない
/// 実行ファイルから呼ぶと、内部のアサーションが失敗してアプリごとクラッシュする
/// (`swift build`/`swift run`で作られる生の実行ファイルはこれに該当する)。
/// `.app`化されるまでの間、通知関連のAPIを呼ぶ前に必ずこれで確認する。
let hasValidBundleForNotifications = Bundle.main.bundleIdentifier != nil

/// WKWebViewはWeb Notification API自体を実装していないため、JS側
/// (`app/static/index.html`・`mobile.html`の`notify()`関数)からの
/// `window.webkit.messageHandlers.notify.postMessage({title, body})`を
/// 受け取り、UserNotificationsフレームワークで実際の通知を出すブリッジ。
final class NotifyBridge: NSObject, WKScriptMessageHandler {
    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard hasValidBundleForNotifications else { return }
        guard let dict = message.body as? [String: String],
              let title = dict["title"], let body = dict["body"] else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }
}

/// 「終了」ボタン(JS側)から、`window.webkit.messageHandlers.appControl.postMessage(...)`
/// 経由でアプリの終了を要求されたときに受け取るブリッジ。JS側は先にPythonサーバーへ
/// /api/shutdownを叩いてサーバープロセスを終了させてから、これを呼んでウィンドウを閉じる
/// (アプリ終了時のapplicationWillTerminateでも子プロセスの後始末をするため、
/// 二重に終了処理が走っても安全)。
final class AppControlBridge: NSObject, WKScriptMessageHandler {
    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard let dict = message.body as? [String: String],
              dict["action"] == "quit" else { return }
        DispatchQueue.main.async {
            NSApp.terminate(nil)
        }
    }
}

/// 書き出し(データのエクスポート)を、Finderの保存パネルで受け取る。
///
/// WKWebViewは showSaveFilePicker に対応しておらず、<a download> も既定では
/// 何も起きない。ダウンロードとして受け取り、保存先とファイル名を選ばせる。
/// これでブラウザ版(Chrome等の showSaveFilePicker)と同じ操作感になる。
/// 保存・読み込みのパネルを、フォルダを選べる形で開くための共通処理。
enum FilePanels {
    /// 前に選んだフォルダを覚えておく鍵。毎回ダウンロードから始まると、
    /// いつも同じ場所に貯める使い方で毎回たどり直すことになる。
    private static let lastFolderKey = "RoastStudioLastExportFolder"

    /// パネルを、ファイルを辿れる大きい形で開かせる。
    ///
    /// NSSavePanelは既定だと名前を入れる欄とSaveボタンだけの小さい形で開き、
    /// フォルダを選べない(開いてから三角印を押さないと出てこない)。
    /// この設定は、OSが「前回どちらの形だったか」を覚えるための場所なので、
    /// 出す前に入れておけば大きい形で開く。
    static func preferExpanded() {
        UserDefaults.standard.set(true, forKey: "NSNavPanelExpandedStateForSaveMode")
        // 名前だけの小さい形で使っていた頃の窓の大きさが残っていると、
        // 大きい形にしても窓が小さいままで結局フォルダが見えない。
        // 一度だけ捨てる(その後は利用者が変えた大きさをそのまま覚える)。
        if !UserDefaults.standard.bool(forKey: "RoastStudioPanelFrameReset") {
            UserDefaults.standard.removeObject(forKey: "NSWindow Frame NSNavPanelAutosaveName")
            UserDefaults.standard.set(true, forKey: "RoastStudioPanelFrameReset")
        }
    }

    /// 最初に開くフォルダ。前に選んだところ、無ければダウンロード。
    static func startFolder() -> URL? {
        if let path = UserDefaults.standard.string(forKey: lastFolderKey),
           FileManager.default.fileExists(atPath: path) {
            return URL(fileURLWithPath: path)
        }
        return FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
    }

    /// 選んだファイルの置き場所を覚える。
    static func remember(_ url: URL) {
        UserDefaults.standard.set(url.deletingLastPathComponent().path, forKey: lastFolderKey)
    }
}


final class DownloadBridge: NSObject, WKDownloadDelegate {
    /// 保存先が決まるまでダウンロード自体を保持しておく(解放されると中断する)
    private var keep: [WKDownload] = []

    func hold(_ download: WKDownload) {
        download.delegate = self
        keep.append(download)
    }

    func download(
        _ download: WKDownload,
        decideDestinationUsing response: URLResponse,
        suggestedFilename: String,
        completionHandler: @escaping (URL?) -> Void
    ) {
        FilePanels.preferExpanded()
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.canCreateDirectories = true
        panel.isExtensionHidden = false
        panel.directoryURL = FilePanels.startFolder()
        panel.message = "書き出したデータの保存先を選んでください"
        // 独立した窓ではなく、アプリのウインドウに付ける。別窓だと、後ろの
        // 画面が触れてしまい、他の窓と重なって分かりにくい。
        let handler: (NSApplication.ModalResponse) -> Void = { result in
            guard result == .OK, let url = panel.url else {
                completionHandler(nil)          // 選ぶのをやめた
                self.keep.removeAll { $0 === download }
                return
            }
            // 同じ名前が既にあれば、保存パネルが上書きの確認を済ませている。
            try? FileManager.default.removeItem(at: url)
            FilePanels.remember(url)
            completionHandler(url)
        }
        if let window = NSApp.mainWindow ?? NSApp.windows.first(where: { $0.isVisible }) {
            panel.beginSheetModal(for: window, completionHandler: handler)
        } else {
            panel.begin(completionHandler: handler)
        }
    }

    func downloadDidFinish(_ download: WKDownload) {
        keep.removeAll { $0 === download }
    }

    func download(_ download: WKDownload, didFailWithError error: Error,
                  resumeData: Data?) {
        keep.removeAll { $0 === download }
        let alert = NSAlert()
        alert.messageText = "書き出せませんでした"
        alert.informativeText = error.localizedDescription
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

/// WKWebViewは、WKUIDelegateを設定しない限りJavaScriptの
/// alert()/confirm()/prompt()を一切表示しない(無言で無視される)。
/// このアプリのGUI(「名前を付けて保存」「削除」「切断」の確認等)は
/// これらに依存しているため、ネイティブのNSAlertに橋渡しする。
final class UIDelegateBridge: NSObject, WKUIDelegate {
    /// `<input type="file">` を押したときのファイル選択。
    ///
    /// これを用意しないと、WKWebViewはファイル選択の要求を黙って捨てる
    /// (押しても何も起きない)。取り込みはこの入力欄を使っている。
    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        FilePanels.preferExpanded()
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.directoryURL = FilePanels.startFolder()
        panel.message = "取り込むファイルを選んでください"
        let handler: (NSApplication.ModalResponse) -> Void = { result in
            guard result == .OK else { completionHandler(nil); return }
            if let first = panel.urls.first { FilePanels.remember(first) }
            completionHandler(panel.urls)
        }
        if let window = webView.window {
            panel.beginSheetModal(for: window, completionHandler: handler)
        } else {
            panel.begin(completionHandler: handler)
        }
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping () -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.runModal()
        completionHandler()
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (Bool) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (String?) -> Void
    ) {
        let alert = NSAlert()
        alert.messageText = prompt
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        let result = alert.runModal()
        completionHandler(result == .alertFirstButtonReturn ? field.stringValue : nil)
    }
}

/// リポジトリのルート(roastlib/ と app/server.py がある場所)を探す。
/// `ROAST_REPO_ROOT` 環境変数があればそれを優先する。
/// Finderからダブルクリックで起動した場合、カレントディレクトリは"/"になり
/// 手がかりにならないため、.appバンドル自身の実際の置き場所を起点に探索する
/// (`swift run` / ビルド済みバイナリのどちらでも、.appはリポジトリ内の
/// 一定の相対位置にあるため確実に見つかる)。
func findRepoRoot() -> String {
    if let env = ProcessInfo.processInfo.environment["ROAST_REPO_ROOT"], !env.isEmpty {
        return env
    }
    let fm = FileManager.default

    func search(from start: String) -> String? {
        var dir = start
        for _ in 0..<8 {
            if fm.fileExists(atPath: dir + "/roastlib") && fm.fileExists(atPath: dir + "/app/server.py") {
                return dir
            }
            let parent = (dir as NSString).deletingLastPathComponent
            if parent == dir { break }
            dir = parent
        }
        return nil
    }

    let bundleDir = (Bundle.main.bundlePath as NSString).deletingLastPathComponent
    if let found = search(from: bundleDir) {
        return found
    }
    if let found = search(from: fm.currentDirectoryPath) {
        return found
    }
    return fm.currentDirectoryPath
}

/// サーバーが既に指定URLで応答しているかを短いタイムアウトで確認する。
/// (start_app.command等で既に起動済みの場合は二重起動しないため)
func isServerRunning(url: URL, timeout: TimeInterval = 1.0) -> Bool {
    let semaphore = DispatchSemaphore(value: 0)
    var ok = false
    var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
    request.timeoutInterval = timeout
    let task = URLSession.shared.dataTask(with: request) { _, response, _ in
        if let http = response as? HTTPURLResponse, http.statusCode < 500 {
            ok = true
        }
        semaphore.signal()
    }
    task.resume()
    _ = semaphore.wait(timeout: .now() + timeout + 0.5)
    return ok
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate,
                         UNUserNotificationCenterDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var serverProcess: Process?
    var startedServer = false
    /// 起動時にTailscaleを使った場合の、そのCLIのパス。アプリ終了時にTailscaleも
    /// 一緒に切断・終了するために保持する(起動スクリプト版と同じ挙動に揃える)。
    var tailscaleCLIUsed: String?
    let repoRoot = findRepoRoot()
    let uiDelegateBridge = UIDelegateBridge()
    let downloadBridge = DownloadBridge()

    func applicationDidFinishLaunching(_ notification: Notification) {
        if hasValidBundleForNotifications {
            // これを設定しないと、アプリが最前面にある間はOSが通知バナーの表示を
            // 黙って抑制してしまう(焙煎中はほぼ常に最前面のため、実質的に
            // 通知が一切届かないのと同じ状態になっていた)。
            UNUserNotificationCenter.current().delegate = self
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, error in
                if let error = error as NSError? {
                    // 過去にこのバンドルID(com.ossanscoffee.roaststudio)で通知を「許可しない」を
                    // 選択したことがあると、OSは以後ダイアログ自体を出さずこのエラーを
                    // 即座に返すようになる(コード側からは再度許可を求める手段が無い)。
                    // ユーザー本人がシステム設定から手動で有効にする必要があるため、
                    // 診断しやすいよう具体的な対処方法をログに残す。
                    if error.domain == UNErrorDomain, error.code == UNError.Code.notificationsNotAllowed.rawValue {
                        NSLog("[通知] 許可が拒否された状態です(過去に「許可しない」を選択した可能性があります)。"
                            + "システム設定 > 通知 > \"Roast Studio\" を開いて手動で有効にしてください"
                            + "(見つからない場合はターミナルで `tccutil reset Notifications com.ossanscoffee.roaststudio` を実行後、アプリを再起動してください)。")
                    } else {
                        NSLog("[通知] 許可のリクエストに失敗: \(error.localizedDescription)")
                    }
                } else {
                    NSLog("[通知] 許可の状態: \(granted ? "許可された" : "拒否された")")
                }
            }
        }

        setupMenu()
        setupWindow()

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.startServerIfNeeded()
        }
    }

    func setupWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1280, height: 860)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Roast Studio"
        window.center()
        window.delegate = self
        window.setFrameAutosaveName("MainWindow")

        let config = WKWebViewConfiguration()
        config.userContentController.add(NotifyBridge(), name: "notify")
        config.userContentController.add(AppControlBridge(), name: "appControl")
        webView = WKWebView(frame: rect, configuration: config)
        webView.uiDelegate = uiDelegateBridge
        // 書き出しをダウンロードとして受け取り、Finderの保存パネルを出すために要る
        webView.navigationDelegate = self
        window.contentView = webView

        showMessage("サーバーを起動しています…")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // ------------------------------------------------------------
    // 書き出し(データのエクスポート)をダウンロードとして受け取る
    // ------------------------------------------------------------
    // 添付ファイルとして返ってきた応答は、画面に表示せずダウンロードに回す。
    // こうしないとWKWebViewはJSONを本文として表示してしまい、保存できない。
    // <a download> のリンクは、そのままでは何も起きない。ダウンロードに回す。
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        if navigationAction.shouldPerformDownload {
            decisionHandler(.download)
            return
        }
        decisionHandler(.allow)
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationResponse: WKNavigationResponse,
        decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
    ) {
        let disposition = (navigationResponse.response as? HTTPURLResponse)?
            .value(forHTTPHeaderField: "Content-Disposition")?.lowercased() ?? ""
        if disposition.hasPrefix("attachment") {
            decisionHandler(.download)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse,
                 didBecome download: WKDownload) {
        downloadBridge.hold(download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction,
                 didBecome download: WKDownload) {
        downloadBridge.hold(download)
    }

    // ============================================================
    // メニューバー
    //   macOS標準の構成(アプリ / 編集 / 表示 / ウインドウ / ヘルプ)に揃える。
    //   以前は「終了」とコピー等の4項目しか無く、⌘Z(取り消し)や⌘R(再読み込み)、
    //   ⌘M(しまう)といったOS標準のショートカットが一切効かなかった。
    //   キー等価文字を登録しておくことで、WKWebView内の入力欄でも標準の
    //   編集操作・ウインドウ操作がそのまま使えるようになる。
    // ============================================================
    func setupMenu() {
        let mainMenu = NSMenu()

        // ---- アプリメニュー(先頭の項目はOSがアプリ名で表示する) ----
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu
        appMenu.addItem(
            withTitle: "Roast Studio について",
            action: #selector(showAboutPanel(_:)),
            keyEquivalent: ""
        ).target = self
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "設定…",
            action: #selector(openSettings(_:)),
            keyEquivalent: ","
        ).target = self
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "Roast Studio を隠す",
            action: #selector(NSApplication.hide(_:)),
            keyEquivalent: "h"
        )
        let hideOthers = appMenu.addItem(
            withTitle: "ほかを隠す",
            action: #selector(NSApplication.hideOtherApplications(_:)),
            keyEquivalent: "h"
        )
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(
            withTitle: "すべてを表示",
            action: #selector(NSApplication.unhideAllApplications(_:)),
            keyEquivalent: ""
        )
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "Roast Studio を終了",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )

        // ---- ファイルメニュー(書き出し・取り込み) ----
        // 画面の「設定」からも同じことができるが、Macでは保存・読み込みが
        // ファイルメニューにあるのが当たり前なので、両方から辿れるようにする。
        let fileMenuItem = NSMenuItem()
        mainMenu.addItem(fileMenuItem)
        let fileMenu = NSMenu(title: "ファイル")
        fileMenuItem.submenu = fileMenu
        fileMenu.addItem(
            withTitle: "上書き保存",
            action: #selector(saveProfileOverwrite(_:)),
            keyEquivalent: "s"
        ).target = self
        let saveAs = fileMenu.addItem(
            withTitle: "名前を付けて保存…",
            action: #selector(saveProfileAs(_:)),
            keyEquivalent: "s"
        )
        saveAs.keyEquivalentModifierMask = [.command, .shift]
        saveAs.target = self
        fileMenu.addItem(.separator())
        // 書き出しも取り込みも、専用の窓の中で選ぶ。項目を分けて並べていた
        // ときは、設定の中の表示へ誘導する形になり、どこを見ればよいのか
        // 分かりにくかった。
        fileMenu.addItem(
            withTitle: "インポート・エクスポート…",
            action: #selector(openImportExport(_:)),
            keyEquivalent: "e"
        ).target = self

        // ---- 編集メニュー(取り消し/やり直しを含む標準構成) ----
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "編集")
        editMenuItem.submenu = editMenu
        editMenu.addItem(
            withTitle: "取り消す",
            action: Selector(("undo:")),
            keyEquivalent: "z"
        )
        let redo = editMenu.addItem(
            withTitle: "やり直す",
            action: Selector(("redo:")),
            keyEquivalent: "z"
        )
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "切り取り", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "貼り付け", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "すべてを選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenu.addItem(.separator())
        // 曲線の編集の取り消し。⌘Zは文字入力の取り消しに残しておきたいので、
        // Optionを足したキーにする。
        let curveUndo = editMenu.addItem(
            withTitle: "プロファイルの編集を取り消す",
            action: #selector(profileUndo(_:)),
            keyEquivalent: "z"
        )
        curveUndo.keyEquivalentModifierMask = [.command, .option]
        curveUndo.target = self
        let curveRedo = editMenu.addItem(
            withTitle: "プロファイルの編集をやり直す",
            action: #selector(profileRedo(_:)),
            keyEquivalent: "z"
        )
        curveRedo.keyEquivalentModifierMask = [.command, .option, .shift]
        curveRedo.target = self
        editMenu.addItem(
            withTitle: "プロファイルを最初に戻す",
            action: #selector(profileReset(_:)),
            keyEquivalent: ""
        ).target = self

        // ---- 焙煎メニュー(送信・調整・選び直し) ----
        // 焙煎中に手が離せない操作(送信・1ハゼ確認)は、画面のどこを見ていても
        // キーだけで届くようにしておく。
        let roastMenuItem = NSMenuItem()
        mainMenu.addItem(roastMenuItem)
        let roastMenu = NSMenu(title: "焙煎")
        roastMenuItem.submenu = roastMenu
        roastMenu.addItem(
            withTitle: "プロファイルを送信",
            action: #selector(sendProfile(_:)),
            keyEquivalent: "\r"
        ).target = self
        let resend = roastMenu.addItem(
            withTitle: "前回のプロファイルを送信",
            action: #selector(resendLastProfile(_:)),
            keyEquivalent: "\r"
        )
        resend.keyEquivalentModifierMask = [.command, .shift]
        resend.target = self
        roastMenu.addItem(.separator())
        let firstCrack = roastMenu.addItem(
            withTitle: "1ハゼ確認",
            action: #selector(markFirstCrack(_:)),
            keyEquivalent: "h"
        )
        firstCrack.keyEquivalentModifierMask = [.command, .shift]
        firstCrack.target = self
        roastMenu.addItem(.separator())
        roastMenu.addItem(
            withTitle: "浅めに調整",
            action: #selector(adjustLighter(_:)),
            keyEquivalent: "["
        ).target = self
        roastMenu.addItem(
            withTitle: "深めに調整",
            action: #selector(adjustDeeper(_:)),
            keyEquivalent: "]"
        ).target = self
        roastMenu.addItem(
            withTitle: "温度オフセット",
            action: #selector(toggleOffset(_:)),
            keyEquivalent: ""
        ).target = self
        roastMenu.addItem(.separator())
        roastMenu.addItem(
            withTitle: "プロファイルを選ぶ…",
            action: #selector(pickProfile(_:)),
            keyEquivalent: "p"
        ).target = self
        roastMenu.addItem(
            withTitle: "焙煎する豆を選ぶ…",
            action: #selector(pickBean(_:)),
            keyEquivalent: "b"
        ).target = self
        roastMenu.addItem(.separator())
        roastMenu.addItem(
            withTitle: "較正を開く",
            action: #selector(openCalibration(_:)),
            keyEquivalent: ""
        ).target = self

        // ---- 表示メニュー(WebViewの再読み込み・表示倍率・フルスクリーン) ----
        let viewMenuItem = NSMenuItem()
        mainMenu.addItem(viewMenuItem)
        let viewMenu = NSMenu(title: "表示")
        viewMenuItem.submenu = viewMenu
        // 右側のタブ(焙煎グラフ・味を推測・豆の情報・焙煎履歴)を⌘1〜⌘4で。
        for (index, tab) in AppDelegate.viewTabs.enumerated() {
            let item = viewMenu.addItem(
                withTitle: tab.title,
                action: #selector(showViewTab(_:)),
                keyEquivalent: String(index + 1)
            )
            item.tag = index
            item.target = self
        }
        viewMenu.addItem(.separator())
        viewMenu.addItem(
            withTitle: "再読み込み",
            action: #selector(reloadPage(_:)),
            keyEquivalent: "r"
        ).target = self
        viewMenu.addItem(.separator())
        viewMenu.addItem(
            withTitle: "拡大",
            action: #selector(zoomIn(_:)),
            keyEquivalent: "+"
        ).target = self
        viewMenu.addItem(
            withTitle: "縮小",
            action: #selector(zoomOut(_:)),
            keyEquivalent: "-"
        ).target = self
        viewMenu.addItem(
            withTitle: "実際のサイズ",
            action: #selector(zoomReset(_:)),
            keyEquivalent: "0"
        ).target = self
        viewMenu.addItem(.separator())
        let fullScreen = viewMenu.addItem(
            withTitle: "フルスクリーンにする",
            action: #selector(NSWindow.toggleFullScreen(_:)),
            keyEquivalent: "f"
        )
        fullScreen.keyEquivalentModifierMask = [.command, .control]
        viewMenu.addItem(.separator())
        viewMenu.addItem(
            withTitle: "モバイル版のQRコード…",
            action: #selector(showMobileQR(_:)),
            keyEquivalent: ""
        ).target = self

        // ---- ウインドウメニュー(OSが「ウインドウ」一覧を自動で差し込む) ----
        let windowMenuItem = NSMenuItem()
        mainMenu.addItem(windowMenuItem)
        let windowMenu = NSMenu(title: "ウインドウ")
        windowMenuItem.submenu = windowMenu
        windowMenu.addItem(
            withTitle: "しまう",
            action: #selector(NSWindow.performMiniaturize(_:)),
            keyEquivalent: "m"
        )
        windowMenu.addItem(
            withTitle: "拡大/縮小",
            action: #selector(NSWindow.performZoom(_:)),
            keyEquivalent: ""
        )
        windowMenu.addItem(.separator())
        windowMenu.addItem(
            withTitle: "すべてを手前に移動",
            action: #selector(NSApplication.arrangeInFront(_:)),
            keyEquivalent: ""
        )
        NSApp.windowsMenu = windowMenu

        // ---- ヘルプメニュー(同梱ドキュメントを開く) ----
        let helpMenuItem = NSMenuItem()
        mainMenu.addItem(helpMenuItem)
        let helpMenu = NSMenu(title: "ヘルプ")
        helpMenuItem.submenu = helpMenu
        helpMenu.addItem(
            withTitle: "はじめにお読みください(安全・免責)",
            action: #selector(openReadme(_:)),
            keyEquivalent: ""
        ).target = self
        helpMenu.addItem(
            withTitle: "ライセンス",
            action: #selector(openLicense(_:)),
            keyEquivalent: ""
        ).target = self
        NSApp.helpMenu = helpMenu

        NSApp.mainMenu = mainMenu
    }

    // ---- メニュー項目の動作 ----

    /// Info.plistのCFBundleName・バージョン・NSHumanReadableCopyright(著作権・
    /// ライセンス・非提携・無保証の表示)がそのまま出るOS標準のAboutパネル。
    @objc func showAboutPanel(_ sender: Any?) {
        NSApp.orderFrontStandardAboutPanel(sender)
        NSApp.activate(ignoringOtherApps: true)
    }

    // ------------------------------------------------------------
    // 書き出し・取り込み(画面の「設定」と同じ処理をメニューから呼ぶ)
    // ------------------------------------------------------------
    @objc func openImportExport(_ sender: Any?) {
        runJS("setTimeout(function(){ openIoDialog(); }, 0); true;",
              failure: "インポート・エクスポートを開けませんでした")
    }

    /// 画面側の exportData() を呼ぶ。書き出しはダウンロードになり、
    /// DownloadBridgeがFinderの保存パネルを出す。
    /// 画面側のJavaScriptを呼ぶ。戻り値は見ない(見る必要のあるものは click が扱う)。
    private func runJS(_ js: String, failure: String) {
        guard let webView = webView else { return }
        webView.evaluateJavaScript(js) { _, error in
            if let error = error {
                self.showIOError(failure, error.localizedDescription)
            }
        }
    }

    // ------------------------------------------------------------
    // 画面のボタンを、メニューから押す
    // ------------------------------------------------------------
    /// 表示メニューに並べる、右側のタブ。順番がそのまま⌘1〜⌘4になる。
    static let viewTabs: [(title: String, id: String)] = [
        ("焙煎グラフ", "tabGraphView"),
        ("味を推測", "tabTasteView"),
        ("豆の情報", "tabBeanInfoView"),
        ("焙煎履歴", "tabRoastHistoryView"),
    ]

    @objc func saveProfileOverwrite(_ sender: Any?) { click("btnOverwriteSave", "上書き保存") }
    @objc func saveProfileAs(_ sender: Any?) { click("btnSave", "名前を付けて保存") }
    @objc func profileUndo(_ sender: Any?) { click("btnUndo", "取り消し") }
    @objc func profileRedo(_ sender: Any?) { click("btnRedo", "やり直し") }
    @objc func profileReset(_ sender: Any?) { click("btnReset", "最初に戻す") }
    @objc func sendProfile(_ sender: Any?) { click("btnSend", "プロファイルの送信") }
    @objc func resendLastProfile(_ sender: Any?) { click("btnResendLast", "前回プロファイルの送信") }
    @objc func markFirstCrack(_ sender: Any?) { click("btnFirstCrack", "1ハゼ確認") }
    @objc func adjustLighter(_ sender: Any?) { click("btnAdjustLighter", "浅めに調整") }
    @objc func adjustDeeper(_ sender: Any?) { click("btnAdjustDeeper", "深めに調整") }
    @objc func toggleOffset(_ sender: Any?) { click("btnOffsetToggle", "温度オフセット") }
    @objc func pickProfile(_ sender: Any?) { click("btnOpenProfileSelect", "プロファイルを選ぶ") }
    @objc func pickBean(_ sender: Any?) { click("btnPickRoastBean", "豆を選ぶ") }
    @objc func openCalibration(_ sender: Any?) { click("btnCalOpen", "較正") }
    @objc func openSettings(_ sender: Any?) { click("btnSettings", "設定") }
    @objc func showMobileQR(_ sender: Any?) { click("btnMobileQR", "モバイル版のQRコード") }

    @objc func showViewTab(_ sender: Any?) {
        guard let item = sender as? NSMenuItem,
              AppDelegate.viewTabs.indices.contains(item.tag) else { return }
        let tab = AppDelegate.viewTabs[item.tag]
        click(tab.id, tab.title)
    }

    /// 画面上のボタンを押す。今その場に無い(隠れている・別の画面にいる)ときは、
    /// 黙って何も起きないと理由が分からないので、その旨を伝える。
    private func click(_ elementID: String, _ label: String) {
        guard let webView = webView else { return }
        let js = """
        (function(){
          var el = document.getElementById(\(jsString(elementID)));
          if(!el) return 'none';
          if(el.disabled) return 'disabled';
          var style = window.getComputedStyle(el);
          if(!el.offsetParent && style.position !== 'fixed') return 'hidden';
          // 押すのは後回しにする。ボタンによっては confirm や prompt が出るので、
          // evaluateJavaScript の内側で動かすと窓が入れ子になる。
          setTimeout(function(){ el.click(); }, 0);
          return 'ok';
        })();
        """
        webView.evaluateJavaScript(js) { result, error in
            if let error = error {
                self.showIOError("\(label)を実行できませんでした", error.localizedDescription)
                return
            }
            switch result as? String {
            case "ok":
                break
            case "disabled":
                self.showIOError("\(label)は今は使えません",
                                 "画面の同じボタンが押せる状態になってから選んでください。")
            default:
                self.showIOError("\(label)は今は使えません",
                                 "この操作のボタンが表示されている画面で選んでください。")
            }
        }
    }

    /// 任意の文字列を、JavaScriptの文字列リテラルとして安全に埋め込む形にする。
    /// 引用符や改行を自前で置き換えると必ず抜けが出るので、JSONに任せる。
    private func jsString(_ value: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: [value], options: []),
              let text = String(data: data, encoding: .utf8) else { return "\"\"" }
        return String(text.dropFirst().dropLast())   // 外側の [ ] を外す
    }

    private func showIOError(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc func reloadPage(_ sender: Any?) {
        webView?.reload()
    }

    /// WKWebViewの表示倍率。0.5〜3.0の範囲に収め、極端な倍率で操作不能に
    /// なるのを防ぐ。
    @objc func zoomIn(_ sender: Any?) {
        guard let webView = webView else { return }
        webView.pageZoom = min(webView.pageZoom + 0.1, 3.0)
    }

    @objc func zoomOut(_ sender: Any?) {
        guard let webView = webView else { return }
        webView.pageZoom = max(webView.pageZoom - 0.1, 0.5)
    }

    @objc func zoomReset(_ sender: Any?) {
        webView?.pageZoom = 1.0
    }

    @objc func openReadme(_ sender: Any?) {
        openRepoFile(candidates: ["はじめにお読みください.txt", "DISCLAIMER.md", "README.md"])
    }

    @objc func openLicense(_ sender: Any?) {
        openRepoFile(candidates: ["LICENSE.ja.txt", "LICENSE"])
    }

    /// リポジトリ直下から最初に見つかったファイルを既定のアプリで開く。
    /// 公開版とローカルの開発用で同梱ファイルが異なるため、候補を順に探す。
    private func openRepoFile(candidates: [String]) {
        let fm = FileManager.default
        for name in candidates {
            let path = repoRoot + "/" + name
            if fm.fileExists(atPath: path) {
                NSWorkspace.shared.open(URL(fileURLWithPath: path))
                return
            }
        }
        let alert = NSAlert()
        alert.messageText = "ファイルが見つかりません"
        alert.informativeText = "同梱ドキュメントが見つかりませんでした（\(candidates.joined(separator: " / "))）。"
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    func showMessage(_ text: String) {
        let escaped = text
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
        let html = """
        <html><body style="font-family:-apple-system,sans-serif;display:flex;
        align-items:center;justify-content:center;height:100vh;margin:0;
        background:#1e1e1e;color:#ddd;text-align:center;white-space:pre-wrap;">
        <p style="max-width:80%;">\(escaped)</p></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    /// 進行状況をウィンドウ内に表示する(バックグラウンドスレッドから呼べる)。
    func setStatus(_ text: String) {
        DispatchQueue.main.async { self.showMessage(text) }
    }

    func startServerIfNeeded() {
        let fm = FileManager.default
        guard fm.fileExists(atPath: repoRoot + "/app/server.py") else {
            DispatchQueue.main.async {
                self.showMessage(
                    "エラー: app/server.py が見つかりません。\n" +
                    "ROAST_REPO_ROOT=\(self.repoRoot)\n\n" +
                    "リポジトリ直下から起動するか、環境変数 ROAST_REPO_ROOT で\n" +
                    "リポジトリの場所を指定してください。"
                )
            }
            return
        }

        // ---- 1. Tailscaleの状態からHTTPS(通知あり)/HTTP(従来版)を決める ----
        var useHTTPS = false
        var httpsHost = ""
        debugLog("==== startServerIfNeeded開始 repoRoot=\(repoRoot) ====")
        if let cli = findTailscaleCLI() {
            debugLog("TailscaleCLI=\(cli)")
            // アプリ終了時にTailscaleも一緒に終了させるため、使ったCLIのパスを控える。
            tailscaleCLIUsed = cli
            setStatus("Tailscaleの状態を確認しています…")
            let state0 = tailscaleBackendState(cli)
            var running = state0 == "Running"
            debugLog("初回BackendState=\(state0 ?? "nil") running=\(running)")
            if !running {
                setStatus("Tailscaleを起動しています…")
                let waitStart = Date()
                running = startTailscaleAndWait(cli)
                debugLog("startTailscaleAndWait結果=\(running) 所要時間=\(Date().timeIntervalSince(waitStart))秒")
            }
            let dns = tailscaleDNSName(cli)
            if running, let dns = dns {
                debugLog("DNSName=\(dns)")
                setStatus("HTTPS証明書を確認しています…(初回は少し時間がかかります)")
                let certStart = Date()
                if issueTailscaleCert(cli, hostname: dns, repoRoot: repoRoot) {
                    useHTTPS = true
                    httpsHost = dns
                    debugLog("証明書発行に成功、HTTPSで起動します 所要時間=\(Date().timeIntervalSince(certStart))秒")
                } else {
                    debugLog("証明書発行に失敗したため、HTTPで起動します 所要時間=\(Date().timeIntervalSince(certStart))秒")
                    setStatus("証明書の発行に失敗したため、通知機能のない従来版(HTTP)で起動します…")
                    Thread.sleep(forTimeInterval: 1.5)
                }
            } else {
                debugLog("running=\(running) dns取得=\(dns ?? "nil") のため、HTTPで起動します")
                setStatus("Tailscaleを起動できなかったため、通知機能のない従来版(HTTP)で起動します…")
                Thread.sleep(forTimeInterval: 1.5)
            }
        } else {
            debugLog("Tailscaleが見つからないため、従来版(HTTP)で起動します")
        }
        debugLog("最終決定: useHTTPS=\(useHTTPS) httpsHost=\(httpsHost)")
        appURL = URL(string: useHTTPS
            ? "https://\(httpsHost):\(serverPort)"
            : "http://127.0.0.1:\(serverPort)")!

        // ---- 2. 既存サーバーの再利用 or 残骸の後始末 ----
        // 期待するURL(スキーム)で正しく応答していればそのまま再利用する。
        // 応答しないのにポートが使われている場合は、前回の終了が不完全で
        // 残った古いサーバー(またはHTTP/HTTPSモードが切り替わった旧サーバー)
        // なので、こちら側のプロセスと確認した上で終了させてから起動する。
        if isServerRunning(url: appURL, timeout: 2.0) {
            DispatchQueue.main.async { self.loadApp() }
            return
        }
        if !pidsListeningOnPort(serverPort).isEmpty {
            setStatus("前回のサーバーが残っていたため、後始末しています…")
            if let errorMessage = cleanupStaleServer(port: serverPort) {
                setStatus(errorMessage)
                return
            }
        }

        // ---- 3. サーバーを起動 ----
        setStatus("サーバーを起動しています…")
        let venvPython = repoRoot + "/venv/bin/python3"
        let useVenv = fm.isExecutableFile(atPath: venvPython)
        let pythonPath = useVenv ? venvPython : "/usr/bin/env"
        var baseArgs = ["-m", "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "\(serverPort)"]
        if useHTTPS {
            baseArgs += ["--ssl-keyfile", "certs/ts.key", "--ssl-certfile", "certs/ts.crt"]
        }
        let arguments = useVenv ? baseArgs : ["python3"] + baseArgs

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.arguments = arguments
        process.currentDirectoryURL = URL(fileURLWithPath: repoRoot)

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if !data.isEmpty {
                FileHandle.standardError.write(data)
            }
        }

        do {
            try process.run()
            serverProcess = process
            startedServer = true
        } catch {
            DispatchQueue.main.async {
                self.showMessage(
                    "エラー: Pythonサーバーの起動に失敗しました。\n\(error.localizedDescription)\n\n" +
                    "venvが用意されているか確認してください:\n\(venvPython)\n\n" +
                    "(先に `python3 -m venv venv && source venv/bin/activate && " +
                    "pip install -r requirements.txt` を実行してください)"
                )
            }
            return
        }

        waitForServerReady()
    }

    func waitForServerReady() {
        for _ in 0..<60 {
            if isServerRunning(url: appURL, timeout: 0.5) {
                DispatchQueue.main.async { self.loadApp() }
                return
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
        DispatchQueue.main.async {
            self.showMessage("サーバーの起動待ちがタイムアウトしました。\nrequirements.txt のインストール状況を確認してください。")
        }
    }

    func loadApp() {
        // WKWebViewのHTTPキャッシュに古いindex.html/JSが残っていると、サーバー側の
        // コードを更新してもアプリを再起動しただけでは反映されないことがあるため、
        // 起動時は常にキャッシュを無視して読み込む。
        webView.load(URLRequest(url: appURL, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData))
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopServerIfNeeded()
        stopTailscaleIfUsed()
    }

    /// 起動時にTailscaleを使っていた場合、アプリ終了時に切断してTailscale自体も終了する
    /// (start_app_https.command と同じ後始末)。
    /// 注意: このアプリ以外の用途でTailscaleを使っていても切断・終了される。
    func stopTailscaleIfUsed() {
        guard let cli = tailscaleCLIUsed else { return }
        NSLog("[終了] Tailscaleを切断・終了します")
        _ = runCommand(cli, ["down"], timeout: 10)
        _ = runCommand("/usr/bin/osascript", ["-e", "quit app \"Tailscale\""], timeout: 10)
    }

    func stopServerIfNeeded() {
        guard startedServer, let process = serverProcess, process.isRunning else { return }
        process.terminate()  // SIGTERM: 通常はuvicornがこれを受けて自分で正常終了する
        // 2026-07追記: waitUntilExit()にタイムアウトが無かったため、BLE切断処理の
        // ハング等でuvicorn側の終了が遅れると、ここで無期限に待ち続けてしまい、
        // アプリを閉じてもPythonサーバープロセスだけバックグラウンドに取り残される
        // (最終的にユーザーがアプリを強制終了すると、子プロセスだけ孤児化して残る)
        // 不具合があった。cleanupStaleServer()と同じく、一定時間待っても終了しない
        // 場合はSIGKILLで確実に後始末する。
        let deadline = Date().addingTimeInterval(5.0)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.2)
        }
        if process.isRunning {
            NSLog("[終了] サーバーがSIGTERMに応答しなかったため強制終了します")
            kill(process.processIdentifier, SIGKILL)
        }
        process.waitUntilExit()
    }

    // アプリが最前面(フォアグラウンド)にある間に届いた通知も、バナー・サウンド
    // 付きで表示する。これを実装しないと、macOSはフォアグラウンド中の通知を
    // デフォルトで無言で握りつぶす(=このアプリを操作しながら焙煎している間、
    // 通知が一切表示されない不具合の原因だった)。
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate

// `kill <pid>` (SIGTERM) やログアウト/シャットダウン時は、Dock/Cmd+Qでの終了と違い
// AppKitの通常の終了フロー(→applicationWillTerminate)を経由しないため、
// Pythonサーバープロセスが子として残り続けてしまう。SIGTERM/SIGINTを
// 明示的に捕まえて後始末してから終了するようにする。
signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let sigtermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigtermSource.setEventHandler {
    delegate.stopServerIfNeeded()
    exit(0)
}
sigtermSource.resume()
let sigintSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
sigintSource.setEventHandler {
    delegate.stopServerIfNeeded()
    exit(0)
}
sigintSource.resume()

app.run()
