/** Evidence from the field (P5.3b): a photo the field attached to a report, a reading in words, and taking both on
 * the phone. Photos are fetched with the project header (an <img src> can't send it) and shown as thumbnails that
 * open full size. */
import { useEffect, useState } from "react";
import { getProject } from "./authState";
import type { FieldReading } from "./api";

/** The photos a case history entry carries (a field report's comment). */
export const photosOf = (data: Record<string, unknown> | null | undefined) =>
  (Array.isArray(data?.attachments) ? data.attachments : [])
    .filter((a): a is { kind: "photo"; exception_id: number; photo_id: number } => !!a && (a as { kind?: string }).kind === "photo");

export const readingText = (r: FieldReading) => `${r.what ? `${r.what} ` : ""}${r.value} ${r.unit}`;

export function FieldPhoto({ exceptionId, photoId }: { exceptionId: number; photoId: number }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    const project = getProject();
    fetch(`/exceptions/${exceptionId}/photos/${photoId}`, project ? { headers: { "X-Fuzer-Project": project } } : undefined)
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => { url = URL.createObjectURL(b); setSrc(url); }, () => setFailed(true));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [exceptionId, photoId]);
  if (failed) return <span className="small muted">photo unavailable</span>;
  if (!src) return <span className="field-photo" aria-busy="true" />;
  return <a className="field-photo" href={src} target="_blank" rel="noreferrer"><img src={src} alt="Photo from the field" /></a>;
}

/** A photo from the phone's camera, made at most 1280 px on its long side as a JPEG, so a report stays small. */
export async function shrink(file: File, max = 1280): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.8);
}

type Recognition = { lang: string; interimResults: boolean; start: () => void; stop: () => void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null; onend: (() => void) | null };

/** Speech to text where the browser offers it (Chrome, Safari): null where it doesn't, so no button is shown. */
export function useDictation(onText: (t: string) => void) {
  const Ctor = (window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition })
    .SpeechRecognition ?? (window as unknown as { webkitSpeechRecognition?: new () => Recognition }).webkitSpeechRecognition;
  const [listening, setListening] = useState<Recognition | null>(null);
  if (!Ctor) return null;
  const toggle = () => {
    if (listening) { listening.stop(); return; }
    const r = new Ctor();
    r.lang = navigator.language || "en";
    r.interimResults = false;
    r.onresult = (e) => onText(Array.from(e.results).map((x) => x[0].transcript).join(" "));
    r.onend = () => setListening(null);
    setListening(r);
    r.start();
  };
  return { listening: !!listening, toggle };
}
