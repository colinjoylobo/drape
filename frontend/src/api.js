const base = "/api";

async function req(path, options = {}) {
  const res = await fetch(base + path, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* response wasn't JSON; the status text is the best available message */
    }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const get = (p) => req(p);
const post = (p, body) => req(p, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const patch = (p, body) => req(p, { method: "PATCH", body: JSON.stringify(body) });
const del = (p) => req(p, { method: "DELETE" });

/** Absolute local path -> a URL the backend will serve. */
export const fileUrl = (p) => (p ? `${base}/file?path=${encodeURIComponent(p)}` : null);

export const api = {
  health: () => get("/health"),

  // sessions
  listSessions: () => get("/sessions"),
  createSession: (name, notes) => post("/sessions", { name, notes }),
  getSession: (id) => get(`/sessions/${id}`),
  sessionShots: (id) => get(`/sessions/${id}/shots`),
  sessionProgress: (id) => get(`/sessions/${id}/progress`),
  updateSession: (id, body) => patch(`/sessions/${id}`, body),
  deleteSession: (id) => del(`/sessions/${id}`),
  importFolder: (id, root, category) =>
    post(`/sessions/${id}/import-folder?root=${encodeURIComponent(root)}` +
         (category ? `&category=${encodeURIComponent(category)}` : "")),
  uploadGarment: (id, form) => req(`/sessions/${id}/garments`, { method: "POST", body: form }),

  // garments
  getGarment: (id) => get(`/garments/${id}`),
  updateGarment: (id, body) => patch(`/garments/${id}`, body),
  deleteGarment: (id) => del(`/garments/${id}`),
  analyze: (id, force = false) =>
    post(`/garments/${id}/analyze?n_looks=2${force ? "&force=true" : ""}`),
  updateAnalysis: (id, body) => patch(`/garments/${id}/analysis`, body),
  setImageRole: (imageId, role) => patch(`/garments/images/${imageId}/role`, { role }),
  addDetailCrop: (id, imageId, box, why) =>
    post(`/garments/${id}/detail-crop?image_id=${imageId}&why=${encodeURIComponent(why || "")}`, box),
  removeDetailCrop: (id, index) => del(`/garments/${id}/detail-crop/${index}`),

  // looks
  proposeLooks: (id, body) => post(`/garments/${id}/looks/propose`, body),
  createLook: (id, body) => post(`/garments/${id}/looks`, body),
  updateLook: (lookId, body) => patch(`/garments/looks/${lookId}`, body),
  deleteLook: (lookId) => del(`/garments/looks/${lookId}`),
  addBackView: (lookId) => post(`/garments/looks/${lookId}/back-view`),

  // avatars
  listAvatars: () => get("/avatars"),
  createAvatar: (body) => post("/avatars", body),
  previewAvatarPrompt: (body) => post("/avatars/preview-prompt", body),
  uploadAvatar: (form) => req("/avatars/upload", { method: "POST", body: form }),
  updateAvatar: (id, body) => patch(`/avatars/${id}`, body),

  // generation
  previewPrompt: (body) => post("/generations/preview", body),
  generate: (body) => post("/generations", body),
  generateBatch: (body) => post("/generations/batch", body),
  rerunQc: (genId) => post(`/generations/${genId}/qc`),
  applyRepair: (body) => post("/generations/repair", body),

  // library
  listLibrary: (category) => get(`/library${category ? `?category=${encodeURIComponent(category)}` : ""}`),
  promoteLook: (genId, sceneTag) =>
    post(`/library/promote/${genId}${sceneTag ? `?scene_tag=${encodeURIComponent(sceneTag)}` : ""}`),
  deleteTemplate: (id) => del(`/library/${id}`),

  // learned lessons — the failure side of the loop
  listLessons: () => get("/library/lessons"),
  updateLesson: (id, body) => patch(`/library/lessons/${id}`, body),
  deleteLesson: (id) => del(`/library/lessons/${id}`),
};

export const CATEGORIES = ["Dresses", "Lingerie", "Nightwear", "Sportswear", "Tops", "Outerwear", "Other"];
export const IMAGE_SIZES = ["portrait_4_3", "auto_2K", "auto_1K", "square_hd"];
// Shoot-craft profiles. v1 is the original prompt behaviour, kept so earlier work
// stays reproducible; v2 adds the campaign craft layer (lighting, lens, posing, gaze).
export const PROFILES = [
  { key: "v2", label: "v2 · campaign craft" },
  { key: "v1", label: "v1 · original" },
];
// Quick-add styling suggestions, grouped so the list stays short and relevant.
// Deliberately generic and quiet — props are meant to support the garment.
export const PROP_SUGGESTIONS = {
  Dresses: ["strappy heels", "fine gold jewellery", "clutch bag", "silk scarf"],
  Lingerie: ["silk robe over one shoulder", "delicate necklace", "bare feet"],
  Nightwear: ["mug of coffee", "open book", "soft throw blanket", "bare feet"],
  Sportswear: ["water bottle", "gym towel", "trainers", "hair tied back"],
  Tops: ["denim jacket", "tote bag", "sunglasses", "layered necklaces"],
  Outerwear: ["leather gloves", "wool scarf", "boots", "shoulder bag"],
  Other: ["tote bag", "sunglasses", "coffee cup", "simple jewellery"],
};

export const ROLES = ["full_front", "full_back", "close_up_detail", "flat_lay_or_other_angle", "irrelevant"];
