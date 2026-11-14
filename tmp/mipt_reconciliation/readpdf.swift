import Foundation
import PDFKit
import AppKit

for file in CommandLine.arguments.dropFirst() {
    guard let doc = PDFDocument(url: URL(fileURLWithPath: file)) else { print("Cannot read \(file)"); continue }
    var text = ""
    for n in 0..<doc.pageCount {
        text += "\n===== PAGE \(n+1) =====\n" + (doc.page(at:n)?.string ?? "")
    }
    try text.write(toFile: file + ".txt", atomically: true, encoding: .utf8)
    print("\(file): \(doc.pageCount) pages, \(text.count) characters")
}
