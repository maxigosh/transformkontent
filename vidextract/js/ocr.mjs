// OCR helper: node ocr.mjs <job.json>
// job = { "lang": "rus", "langPath": "/dir/with/traineddata", "psm": "7", "images": ["a.png", ...] }
// prints [{ "text": "...", "confidence": 93 }, ...] to stdout
import { readFileSync } from "node:fs";
import { createWorker } from "tesseract.js";

const job = JSON.parse(readFileSync(process.argv[2], "utf8"));
const worker = await createWorker(job.lang, 1, {
  langPath: job.langPath,
  cachePath: job.langPath,
  gzip: false,
});
await worker.setParameters({ tessedit_pageseg_mode: job.psm ?? "7" });
const out = [];
for (const img of job.images) {
  const { data } = await worker.recognize(img);
  out.push({ text: data.text.trim(), confidence: data.confidence });
}
await worker.terminate();
process.stdout.write(JSON.stringify(out));
