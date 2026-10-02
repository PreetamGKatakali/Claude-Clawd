// Clawd in the macOS menu bar, plus a notification when Claude Code needs your
// approval. Built on your Mac by scripts/menubar.py with the system swiftc; no
// third-party code.
//
// It reads the companion server's /state snapshot on 127.0.0.1 only, the same
// allowlisted payload the window gets, and starts that server if it is not
// running. It quits by itself when the menu_bar option is turned off.
//
//   ClawdBar --base DIR --python PATH --server PATH [--terminal BUNDLE_ID]
import Cocoa
import UserNotifications

let GRIDS: [String: [String]] = [
    "BASE":    ["..BBBBBBBBBB..", "..BB.BBBB.BB..", "BBBBBBBBBBBBBB", "..B.B....B.B.."],
    "BLINK":   ["..BBBBBBBBBB..", "..BBBBBBBBBB..", "BBBBBBBBBBBBBB", "..B.B....B.B.."],
    "THINK_A": ["..BBBBBBBBBB..", "..B.BBBB.BBB..", "BBBBBBBBBBBBBB", "..B.B....B.B.."],
    "THINK_B": ["..BBBBBBBBBB..", "..BBB.BBBB.B..", "BBBBBBBBBBBBBB", "..B.B....B.B.."],
    "ARM_UP":  ["..BBBBBBBBBB.B", "..BB.BBBB.BBBB", "BBBBBBBBBBBB..", "..B.B....B.B.."],
    "HAPPY":   ["B.BBBBBBBBBB.B", "BBBB.BBBB.BBBB", "..BBBBBBBBBB..", "..B.B....B.B.."],
    "DONE_B":  ["S.BBBBBBBBBB.S", "..BB.BBBB.BB..", "BBBBBBBBBBBBBB", "..B.B....B.B.."],
    "WAVE_A":  ["..BBBBBBBBBB.B..", "..BB.BBBB.BBBB..", "BBBBBBBBBBBB....", "..B.B....B.B...."],
    "WAVE_B":  ["..BBBBBBBBBB..BB", "..BB.BBBB.BB.BB.", "BBBBBBBBBBBBBB..", "..B.B....B.B...."],
    "HYD_A":   ["..BBBBBBBBBB.....G..G.", "..BB.BBBB.BB.....GWWG.", "BBBBBBBBBBBBBB...GWWG.", "..B.B....B.B.....GWWG."],
    "HYD_B":   ["..BBBBBBBBBB.....G..G.", "..BB.BBBB.BB.....G..G.", "BBBBBBBBBBBBBB...GWWG.", "..B.B....B.B.....GWWG."],
]

func rgb(_ v: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255,
            blue: CGFloat(v & 0xFF) / 255, alpha: 1)
}
let CLASSIC: UInt32 = 0xD97757
let OFFLINE: UInt32 = 0xA8A8AE
let EXTRA: [Character: NSColor] = ["S": rgb(0xFBCF4F), "G": rgb(0x818CF8), "W": rgb(0xC7D2FE)]
let GOLD = rgb(0xFBCF4F)

func hexColor(_ s: Any?) -> UInt32? {
    guard let s = s as? String, s.count == 7, s.hasPrefix("#") else { return nil }
    return UInt32(s.dropFirst(), radix: 16)
}

func sprite(_ name: String, body: NSColor, badge: Bool) -> NSImage {
    let grid = GRIDS[name] ?? GRIDS["BASE"]!
    let px: CGFloat = 3
    let w = CGFloat(grid[0].count) * px + (badge ? 8 : 0)
    let h = CGFloat(grid.count) * px
    let img = NSImage(size: NSSize(width: w, height: h), flipped: true) { _ in
        for (r, row) in grid.enumerated() {
            for (c, ch) in row.enumerated() where ch != "." {
                (EXTRA[ch] ?? body).setFill()
                NSRect(x: CGFloat(c) * px, y: CGFloat(r) * px, width: px, height: px).fill()
            }
        }
        if badge {  // gold dot: Claude Code is waiting for you
            GOLD.setFill()
            NSBezierPath(ovalIn: NSRect(x: w - 6, y: 0, width: 6, height: 6)).fill()
        }
        return true
    }
    img.isTemplate = false
    return img
}

func arg(_ name: String) -> String? {
    let a = CommandLine.arguments
    guard let i = a.firstIndex(of: name), i + 1 < a.count else { return nil }
    return a[i + 1]
}

final class App: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    let base = arg("--base") ?? (NSHomeDirectory() + "/.claude/clawd-companion")
    let python = arg("--python") ?? "/usr/bin/python3"
    let server = arg("--server")
    let terminal = arg("--terminal") ?? "com.apple.Terminal"
    let canNotify = Bundle.main.bundleIdentifier != nil

    var item: NSStatusItem!
    var snap: [String: Any]? = nil
    var lastState: String? = nil
    var tick = 0
    var serverProc: Process? = nil
    var lastServerTry = Date.distantPast
    var polling = false
    let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        writePid()
        // menubar.py stop sends SIGTERM; quit through NSApp so the clean-up runs.
        signal(SIGTERM, SIG_IGN)
        sigterm.setEventHandler { NSApp.terminate(nil) }
        sigterm.resume()
        if canNotify {
            let c = UNUserNotificationCenter.current()
            c.delegate = self
            c.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.step() }
        poll()
        redraw()
    }

    func applicationWillTerminate(_ n: Notification) {
        serverProc?.terminate()  // only a server this app started
        try? FileManager.default.removeItem(atPath: base + "/menubar.pid")
    }

    func writePid() {
        try? "\(getpid())\n".write(toFile: base + "/menubar.pid", atomically: true, encoding: .utf8)
    }

    // MARK: state

    func step() {
        tick += 1
        if tick % 2 == 0 {
            if !enabled() { NSApp.terminate(nil); return }
            poll()
        }
        redraw()
    }

    /// The menu_bar option, as the hooks mirror it into config.json.
    func enabled() -> Bool {
        guard let d = FileManager.default.contents(atPath: base + "/config.json"),
              let cfg = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return true }
        return (cfg["menu_bar"] as? Bool) ?? true
    }

    func port() -> Int? {
        guard let s = try? String(contentsOfFile: base + "/port", encoding: .utf8) else { return nil }
        return Int(s.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func poll() {
        if polling { return }
        guard let p = port(), let url = URL(string: "http://127.0.0.1:\(p)/state") else {
            snap = nil
            startServer()
            return
        }
        polling = true
        var req = URLRequest(url: url)
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { data, _, err in
            let obj = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                self.polling = false
                self.snap = obj
                if obj == nil || err != nil { self.startServer() }
                self.changed()
                self.redraw()
            }
        }.resume()
    }

    func startServer() {
        guard let server = server, FileManager.default.fileExists(atPath: server),
              Date().timeIntervalSince(lastServerTry) > 10 else { return }
        lastServerTry = Date()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [server, "--serve"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        if (try? p.run()) != nil { serverProc = p }
    }

    func state() -> String {
        guard let s = snap else { return "offline" }
        let st = s["state"] as? String ?? "idle"
        if st == "idle", let until = s["welcome_until"] as? Double, let now = s["now"] as? Double, now < until {
            return "welcome"
        }
        return st
    }

    func changed() {
        let st = state()
        if st == "confirm" && lastState != "confirm" { notifyApproval() }
        lastState = st
    }

    // MARK: drawing

    func frames(_ st: String) -> [String] {
        switch st {
        case "thinking": return ["THINK_A", "THINK_B"]
        case "confirm": return ["ARM_UP", "BASE"]
        case "done": return ["HAPPY", "DONE_B"]
        case "hydrate": return ["HYD_A", "HYD_B"]
        case "welcome": return ["WAVE_A", "WAVE_B"]
        case "offline": return ["BLINK"]
        default: return ["BASE", "BASE", "BASE", "BLINK"]
        }
    }

    func label(_ st: String) -> String {
        switch st {
        case "thinking":
            if let t = snap?["topic"] as? String, !t.isEmpty { return "working on \(t)" }
            return "working"
        case "confirm": return "needs your approval"
        case "done": return "done"
        case "hydrate": return "time to drink water"
        case "welcome": return "hello!"
        case "offline": return "waiting for Claude Code"
        default: return "ready"
        }
    }

    func redraw() {
        let st = state()
        let f = frames(st)
        let color = st == "offline" ? OFFLINE : (hexColor(snap?["color"]) ?? CLASSIC)
        let b = item.button!
        b.image = sprite(f[tick % f.count], body: rgb(color), badge: st == "confirm")
        b.imagePosition = .imageLeft
        b.title = st == "confirm" ? " needs you" : ""
        b.toolTip = "Clawd: " + label(st)
        item.menu = buildMenu(st)
    }

    func buildMenu(_ st: String) -> NSMenu {
        let m = NSMenu()
        m.addItem(disabled("Clawd · " + label(st)))
        if let project = snap?["project"] as? String, !project.isEmpty {
            let n = snap?["sessions"] as? Int ?? 0
            m.addItem(disabled(project + (n > 1 ? "  ·  \(n) sessions" : "")))
        }
        m.addItem(.separator())
        let open = action("Open Clawd window", #selector(openWindow))
        open.isEnabled = port() != nil
        m.addItem(open)
        m.addItem(action("Bring \(terminalName()) to front", #selector(openTerminal)))
        m.addItem(action("Send a test notification", #selector(testNote)))
        m.addItem(.separator())
        m.addItem(action("Hide until next session", #selector(hide)))
        return m
    }

    func disabled(_ t: String) -> NSMenuItem {
        let i = NSMenuItem(title: t, action: nil, keyEquivalent: ""); i.isEnabled = false; return i
    }
    func action(_ t: String, _ s: Selector) -> NSMenuItem {
        let i = NSMenuItem(title: t, action: s, keyEquivalent: ""); i.target = self; return i
    }

    // MARK: actions

    func terminalURL() -> URL? { NSWorkspace.shared.urlForApplication(withBundleIdentifier: terminal) }

    func terminalName() -> String {
        guard let u = terminalURL() else { return "Terminal" }
        return FileManager.default.displayName(atPath: u.path).replacingOccurrences(of: ".app", with: "")
    }

    @objc func openTerminal() {
        guard let u = terminalURL() else { return }
        NSWorkspace.shared.openApplication(at: u, configuration: NSWorkspace.OpenConfiguration())
    }

    @objc func openWindow() {
        guard let p = port(), let u = URL(string: "http://127.0.0.1:\(p)/") else { return }
        NSWorkspace.shared.open(u)
    }

    @objc func hide() { NSApp.terminate(nil) }

    @objc func testNote() {
        post("Clawd test notification", "This is how an approval alert will look.")
    }

    // MARK: notifications

    func notifyApproval() {
        // You are already looking at the terminal: the prompt is right there.
        if NSWorkspace.shared.frontmostApplication?.bundleIdentifier == terminal { return }
        let project = (snap?["project"] as? String).map { " in " + $0 } ?? ""
        post("Clawd needs your approval", "Claude Code is waiting for you\(project). Click to switch to \(terminalName()).")
    }

    func post(_ title: String, _ body: String) {
        guard canNotify else { return fallback(title, body) }
        let c = UNUserNotificationCenter.current()
        c.getNotificationSettings { s in
            guard s.authorizationStatus == .authorized || s.authorizationStatus == .provisional else {
                return self.fallback(title, body)
            }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            c.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)) { err in
                if err != nil { self.fallback(title, body) }
            }
        }
    }

    /// AppleScript notification when the native one is refused. Clicking it
    /// does not switch to the terminal.
    func fallback(_ title: String, _ body: String) {
        let esc = { (s: String) in s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", "display notification \"\(esc(body))\" with title \"\(esc(title))\""]
        try? p.run()
    }

    func userNotificationCenter(_ c: UNUserNotificationCenter, willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound])
    }

    func userNotificationCenter(_ c: UNUserNotificationCenter, didReceive r: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        DispatchQueue.main.async { self.openTerminal() }
        done()
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.run()
