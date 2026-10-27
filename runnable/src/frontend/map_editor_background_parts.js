// Join the parts of one background answer and decode the image at its reported pixel size (A9, 3.16).
"use strict";

// One discarded transfer reads as an unsupported image; the caller shows that reason.
function mapBackgroundPartsError(reason) {
  const error = new Error(window.ServerManMapWording.displayReason("image", {reason}));
  error.result = {error: {details: {reason}}};
  return error;
}

// Read the parts in order, one at a time; current() is false after a newer choice, load or close.
async function mapBackgroundPartsRead(first, read, current) {
  if (first?.available !== true || first.part !== 0 || !Number.isSafeInteger(first.parts) || first.parts < 1) {
    throw mapBackgroundPartsError("part_unknown");
  }
  const texts = [first.data];
  for (let part = 1; part < first.parts; part += 1) {
    const value = await read(part);
    if (!current()) return null;
    // Every part repeats the identity of the whole file; a different file discards the image
    if (value?.available !== true || value.part !== part || value.parts !== first.parts ||
        value.sha256 !== first.sha256 || value.byte_size !== first.byte_size) {
      throw mapBackgroundPartsError("unsupported_image_type");
    }
    texts.push(value.data);
  }
  // The joined text sets a data URL image source; the decode must give the reported pixel size
  const image = new Image();
  image.src = `data:${first.media_type};base64,${texts.join("")}`;
  try {
    await image.decode();
  } catch (error) {
    throw mapBackgroundPartsError("unsupported_image_type");
  }
  if (!current()) return null;
  if (image.naturalWidth !== first.pixel_width || image.naturalHeight !== first.pixel_height) {
    throw mapBackgroundPartsError("unsupported_image_type");
  }
  return {image, file_name: first.file_name, sha256: first.sha256, byte_size: first.byte_size,
    pixel_width: first.pixel_width, pixel_height: first.pixel_height};
}

// The first answer comes from the host dialog or from the stored-image read of part 0.
window.ServerManMapBackgroundParts = Object.freeze({read: mapBackgroundPartsRead, error: mapBackgroundPartsError});
