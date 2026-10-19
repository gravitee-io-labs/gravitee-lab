// The images a job downloads, as the drawer draws them: a bar, a percentage and the bytes of each, and the speed of all

import { esc } from "../core.js";

// Bytes as Docker's own pulls say them, in decimal units: "412 MB", "1.8 GB"
function bytes(count) {
  if (count >= 1e9) return `${(count / 1e9).toFixed(1)} GB`;
  if (count >= 1e6) return `${count >= 1e7 ? Math.round(count / 1e6) : (count / 1e6).toFixed(1)} MB`;
  return count >= 1e3 ? `${Math.round(count / 1e3)} kB` : `${count} B`;
}

// How far an image is, in whole percents; null while its size is not known
const percent = ({ current, total }) => (total > 0 ? Math.min(100, Math.floor((current / total) * 100)) : null);

// What the downloads of a job come to: "412 MB of 1.8 GB at 18 MB/s". The speed is left out while nothing is going down,
// and so is all of it once every image is there.
export function totals({ images, speed }) {
  const going = images.filter((image) => image.state === "downloading" && image.total > 0);
  if (!going.length) return "";
  const done = going.reduce((sum, image) => sum + image.current, 0), all = going.reduce((sum, image) => sum + image.total, 0);
  return `${bytes(done)} of ${bytes(all)}${speed ? ` at ${bytes(speed)}/s` : ""}`;
}

// The status of an image that is being pulled: its bar, its percentage, and its bytes. Once it is unpacking there is
// nothing to count, and it says so.
export function pullStatus(name, { state, current, total }) {
  const known = total > 0, share = state === "extracting" ? 100 : known ? percent({ current, total }) : 0;
  const words = state === "extracting" ? "Extracting" : known ? `${bytes(current)} / ${bytes(total)}` : "Pulling";
  return `<span class="job-step-status pull"><span class="progress" role="progressbar" aria-label="${esc(name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${share}"><span style="width: ${share}%"></span></span>`
    + `<span class="pull-percent">${state === "downloading" && known ? `${share}%` : ""}</span><span class="pull-size">${esc(words)}</span></span>`;
}
