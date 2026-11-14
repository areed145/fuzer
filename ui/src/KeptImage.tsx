/** An image the field pack may have kept (P5.17c): from the server as usual, and from the phone when that fails
 * without signal. */
import { useEffect, useState, type ImgHTMLAttributes } from "react";
import { keptAnswer } from "./fieldPack";

export function KeptImage({ src, ...rest }: ImgHTMLAttributes<HTMLImageElement> & { src: string }) {
  const [shown, setShown] = useState(src);
  useEffect(() => setShown(src), [src]);
  useEffect(() => () => { if (shown.startsWith("blob:")) URL.revokeObjectURL(shown); }, [shown]);
  const fallback = async () => {
    if (shown !== src) return;
    const kept = await keptAnswer<{ type: string; bytes: ArrayBuffer }>(src);
    if (kept) setShown(URL.createObjectURL(new Blob([kept.bytes], { type: kept.type })));
  };
  return <img {...rest} src={shown} onError={() => void fallback()} />;
}
