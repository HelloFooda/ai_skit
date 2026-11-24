/**
 * 资产出图（S7 设定图）的共享逻辑：资产库与对话页章节资产抽屉共用同一套
 * 预览 → 确认 → 提交 → 选版流程，确保确认码 / 付费闸门 / 额度熔断只有一份实现。
 * 只管状态与请求，不碰各页面自己的列表与抽屉开合。
 */
import { ref } from "vue";
import {
  generateMediaStream,
  getMediaSidecar,
  listMediaImages,
  previewMedia,
  selectMediaImage,
  type MediaImageRecord,
  type MediaPreview,
  type ProgressEvent,
} from "./api";

export function useAssetMedia(notify?: (message: string) => void) {
  /** prompt_id → 该条目已出的全部版本；首屏一次性读回，避免逐条请求 */
  const imagesByPrompt = ref<Record<string, MediaImageRecord[]>>({});
  /** 确认抽屉：预览回告（算账不花钱），用户点确认才提交 */
  const mediaPreview = ref<MediaPreview | null>(null);
  const mediaLoading = ref(false);
  const mediaBusy = ref(false);
  const mediaError = ref("");
  const mediaStep = ref("");
  const mediaSidecar = ref<Record<string, unknown> | null>(null);
  const lightbox = ref<MediaImageRecord | null>(null);

  /** 清单里的条目 → 该条目已出的图；一条目可有多版，selected 是当前版 */
  function imagesOf(promptId: string): MediaImageRecord[] {
    return imagesByPrompt.value[promptId] ?? [];
  }

  function currentImage(promptId: string): MediaImageRecord | null {
    const list = imagesOf(promptId);
    return list.find((item) => item.selected) ?? list[list.length - 1] ?? null;
  }

  async function loadImages(novel: string) {
    if (!novel) return;
    try {
      const data = await listMediaImages(novel);
      const buckets: Record<string, MediaImageRecord[]> = {};
      for (const image of data.images) {
        (buckets[image.prompt_id] ??= []).push(image);
      }
      for (const key of Object.keys(buckets)) {
        buckets[key].sort((a, b) => a.version - b.version);
      }
      imagesByPrompt.value = buckets;
    } catch {
      // 出图索引读失败不该拖垮资产主体：静默降级为「没有图」。
      imagesByPrompt.value = {};
    }
  }

  /** 打开确认抽屉：先算账（不花钱），把指纹、确认码、阻塞项、额度都摆出来 */
  async function openMedia(promptId: string) {
    mediaPreview.value = null;
    mediaError.value = "";
    mediaStep.value = "";
    mediaSidecar.value = null;
    mediaLoading.value = true;
    try {
      mediaPreview.value = await previewMedia(promptId);
    } catch (e: unknown) {
      mediaError.value = e instanceof Error ? e.message : String(e);
    } finally {
      mediaLoading.value = false;
    }
  }

  /** 确认后提交：SSE 逐张回推，落盘一张显示一张 */
  async function confirmMedia(novel: string) {
    const preview = mediaPreview.value;
    if (!preview || mediaBusy.value) return;
    mediaBusy.value = true;
    mediaError.value = "";
    mediaStep.value = "提交中…";
    try {
      await generateMediaStream(preview.prompt_id, preview.confirmation, {
        onProgress: (event: ProgressEvent) => {
          mediaStep.value = event.text;
        },
        onMedia: (image) => {
          const list = imagesByPrompt.value[image.prompt_id] ?? [];
          imagesByPrompt.value = {
            ...imagesByPrompt.value,
            [image.prompt_id]: [...list.filter((i) => i.id !== image.id), image].sort(
              (a, b) => a.version - b.version,
            ),
          };
        },
        onDone: (payload) => {
          mediaStep.value = payload.cached ? "命中已有产物，未重复提交" : "完成";
          notify?.(
            payload.cached ? "同参数已有成品，未重复扣费" : "出图完成，旧版本保留可回退",
          );
          // 收尾再拉一次，确保 selected 标记与清单一致
          void loadImages(novel);
          setTimeout(() => {
            mediaPreview.value = null;
            mediaStep.value = "";
          }, 900);
        },
        onError: (message) => {
          mediaError.value = message;
          mediaStep.value = "";
        },
      });
    } catch (e: unknown) {
      mediaError.value = e instanceof Error ? e.message : String(e);
      mediaStep.value = "";
    } finally {
      mediaBusy.value = false;
    }
  }

  /** 切回某一版：同条目其它版本自动取消选用，旧图不删 */
  async function pickImage(image: MediaImageRecord, novel: string) {
    try {
      await selectMediaImage(image.id);
      await loadImages(novel);
      notify?.(`已选用 v${image.version}`);
    } catch (e: unknown) {
      mediaError.value = e instanceof Error ? e.message : String(e);
    }
  }

  async function showSidecar(image: MediaImageRecord) {
    mediaSidecar.value = null;
    try {
      mediaSidecar.value = await getMediaSidecar(image.id);
    } catch (e: unknown) {
      mediaError.value = e instanceof Error ? e.message : String(e);
    }
  }

  function closeMedia() {
    if (mediaBusy.value) return;
    mediaPreview.value = null;
    mediaError.value = "";
    mediaStep.value = "";
  }

  return {
    imagesByPrompt,
    mediaPreview,
    mediaLoading,
    mediaBusy,
    mediaError,
    mediaStep,
    mediaSidecar,
    lightbox,
    imagesOf,
    currentImage,
    loadImages,
    openMedia,
    confirmMedia,
    pickImage,
    showSidecar,
    closeMedia,
  };
}
