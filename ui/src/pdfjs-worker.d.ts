// Vite serves the pdf.js worker as its own file and hands back its URL.
declare module "pdfjs-dist/build/pdf.worker.min.mjs?url" {
  const url: string;
  export default url;
}
