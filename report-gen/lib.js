// Общий генератор отчётов по лабораторным — оформление по образцу Lab0_Мусин_Амир.docx
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, ExternalHyperlink, Table, TableRow, TableCell,
  ImageRun, AlignmentType, WidthType, BorderStyle, ShadingType, VerticalAlign, PageBreak,
} = require("docx");

const STUDENT = {
  fio: "Жанайдаров Амирлан",
  group: "M094–6121–26–02",
  teacher: "Эгамбердиев Э.У.",
  city: "Астана 2026",
};

const BODY = { after: 140, line: 276 };
const SZ = 24, SZ_CODE = 22, SZ_TBL = 22;

// Разбор строки: `код` → Courier New 11, **жирный** → bold
function runs(text, base = {}) {
  const out = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), size: SZ, ...base }));
    const tok = m[0];
    if (tok.startsWith("`")) out.push(new TextRun({ text: tok.slice(1, -1), font: "Courier New", size: base.size ? base.size - 2 : SZ_CODE }));
    else out.push(new TextRun({ text: tok.slice(2, -2), bold: true, size: SZ, ...base }));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), size: SZ, ...base }));
  return out;
}

const blank = () => new Paragraph({ children: [] });
const center = (text, size, bold = false) =>
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text, size, bold })] });

function titlePage(no, title) {
  return [
    center("Евразийский национальный университет им. Л.Н. Гумилева", 32, true),
    center("Институт цифровых наук и искусственного интеллекта", 32, true),
    center("Кафедра Компьютерная и программная инженерия", 32, true),
    blank(), blank(), blank(), blank(),
    center("ОТЧЁТ ПО ЛАБОРАТОРНОЙ РАБОТЕ", 28, true),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [
        new TextRun({ text: "по дисциплине «Архитектура IoT систем и технологии передачи данных»", size: 32, break: 1 }),
        new TextRun({ text: `Лабораторная работа № ${no} `, size: 32, break: 2 }),
        new TextRun({ text: `«${title}»`, size: 32, bold: true }),
      ],
    }),
    blank(), blank(), blank(), blank(), blank(),
    center(`Выполнил(а): студент(ка) группы ${STUDENT.group}, ${STUDENT.fio}`, 28),
    center("______________________________________", 28),
    blank(), blank(),
    center(`Проверил: ${STUDENT.teacher}`, 28),
    blank(), blank(), blank(),
    center(STUDENT.city, 32, true),
  ];
}

const H = (text, pageBreak = false) => new Paragraph({
  alignment: AlignmentType.CENTER, spacing: { before: 240, after: 240 }, keepNext: true,
  children: [...(pageBreak ? [new PageBreak()] : []), new TextRun({ text, bold: true, size: 28 })],
});

const P = (text) => new Paragraph({
  alignment: AlignmentType.JUSTIFIED, indent: { firstLine: 720 }, spacing: BODY, children: runs(text),
});

const Step = (text) => new Paragraph({ indent: { left: 360, hanging: 360 }, spacing: BODY, children: runs(text) });

const Code = (text) => new Paragraph({
  indent: { left: 360, hanging: 360 }, spacing: BODY,
  children: [new TextRun({ text, font: "Courier New", size: SZ_CODE })],
});

const QA = (n, q, a) => new Paragraph({
  alignment: AlignmentType.JUSTIFIED, indent: { firstLine: 720 }, spacing: BODY,
  children: [new TextRun({ text: `${n}. ${q}`, bold: true, size: SZ }), new TextRun({ text: " ", size: SZ }), ...runs(a)],
});

function Link(label, url) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED, indent: { firstLine: 720 }, spacing: BODY,
    children: [
      new TextRun({ text: label + " ", size: SZ }),
      new ExternalHyperlink({ link: url, children: [new TextRun({ text: url, size: SZ, style: "Hyperlink" })] }),
    ],
  });
}

const caption = (text) => new Paragraph({ spacing: { before: 200, after: 120 }, keepNext: true, children: [new TextRun({ text, size: SZ })] });

const BORDER = { style: BorderStyle.SINGLE, size: 4, color: "808080" };
function cell(text, width, header) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    borders: { top: BORDER, bottom: BORDER, left: BORDER, right: BORDER },
    shading: header ? { type: ShadingType.CLEAR, color: "auto", fill: "D9D9D9" } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    verticalAlign: VerticalAlign.CENTER,
    children: String(text).split("\n").map((line) => new Paragraph({
      alignment: header ? AlignmentType.CENTER : AlignmentType.LEFT,
      children: runs(line, { size: SZ_TBL, bold: header || undefined }),
    })),
  });
}

function Tbl(title, widths, header, rows) {
  const total = widths.reduce((a, b) => a + b, 0);
  return [
    caption(title),
    new Table({
      width: { size: total, type: WidthType.DXA },
      columnWidths: widths,
      rows: [
        new TableRow({ tableHeader: true, children: header.map((h, i) => cell(h, widths[i], true)) }),
        ...rows.map((r) => new TableRow({ children: r.map((c, i) => cell(c, widths[i], false)) })),
      ],
    }),
  ];
}

// PNG: ширина и высота из заголовка IHDR
function pngSize(buf) { return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) }; }

function Fig(path, title, maxH = 900) {
  const data = fs.readFileSync(path);
  const { w, h } = pngSize(data);
  const maxW = 605; // ≈ 16 см при 96 dpi — как в образце
  let width = Math.min(maxW, Math.round(w * 0.75));
  let height = Math.round(h * width / w);
  if (height > maxH) { width = Math.round(width * maxH / height); height = maxH; }
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 }, keepNext: true,
      children: [new ImageRun({ type: "png", data, transformation: { width, height } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 60, after: 200 },
      children: [new TextRun({ text: title, size: SZ })] }),
  ];
}

async function build(outPath, no, title, body) {
  const doc = new Document({
    creator: STUDENT.fio,
    title: `Лабораторная работа № ${no}. ${title}`,
    styles: { default: { document: { run: { font: "Times New Roman", size: SZ } } } },
    sections: [{
      properties: { page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1134, right: 850, bottom: 1134, left: 1701, header: 708, footer: 708 },
      } },
      children: [...titlePage(no, title), ...body],
    }],
  });
  fs.writeFileSync(outPath, await Packer.toBuffer(doc));
  console.log("written", outPath);
}

module.exports = { build, H, P, Step, Code, QA, Link, Tbl, Fig, STUDENT };
