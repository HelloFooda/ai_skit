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
  refineMedia,
  saveAssetPrompt,
  selectMediaImage,
  type MediaImageRecord,
  type MediaPreview,
  type MediaRefineDraft,
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

  /** 图生图迭代：要迭代的旧图 + 用户追加描述 + LLM 融合草案 */
  const refineSource = ref<MediaImageRecord | null>(null);
  const refineAddition = ref("");
  const refineDraft = ref<MediaRefineDraft | null>(null);
  const refineLoading = ref(false);
  const refineApplying = ref(false);
  /** 本次出图绑定的参考图 id；preview / generate 都靠它算同一指纹 */
  const activeReferenceId = ref("");

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
      mediaPreview.value = await previewMedia(promptId, null, activeReferenceId.value);
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

  /** 打开迭代：以某一张已出图为参考，填追加描述重新出图 */
  function startRefine(image: MediaImageRecord) {
    refineSource.value = image;
    refineAddition.value = "";
    refineDraft.value = null;
    mediaError.value = "";
  }

  /** 第一步：追加描述 → LLM 补丁式融合，只出草案，不落盘不出图 */
  async function buildRefineDraft() {
    const source = refineSource.value;
    if (!source || !refineAddition.value.trim() || refineLoading.value) return;
    refineLoading.value = true;
    mediaError.value = "";
    refineDraft.value = null;
    try {
      refineDraft.value = await refineMedia(
        source.prompt_id,
        refineAddition.value.trim(),
        source.id,
      );
    } catch (e: unknown) {
      mediaError.value = e instanceof Error ? e.message : String(e);
    } finally {
      refineLoading.value = false;
    }
  }

  /**
   * 第二步：用户看过 diff 确认后，把融合正文原样写回，再带这张旧图重新预览。
   * 正文一改指纹就变，旧确认码自动失效——新确认码来自这里的新 preview。
   */
  async function applyRefine() {
    const draft = refineDraft.value;
    const source = refineSource.value;
    if (!draft || !source || refineApplying.value) return;
    refineApplying.value = true;
    mediaError.value = "";
    mediaStep.value = "写回正文并重新算账…";
    try {
      await saveAssetPrompt(draft.prompt_id, draft.after_markdown);
      activeReferenceId.value = source.id;
      mediaPreview.value = await previewMedia(draft.prompt_id, null, source.id);
      // 草案已兑现，收起迭代面板；确认抽屉里显示的是融合后正文 + 参考图的新账
      refineDraft.value = null;
      refineSource.value = null;
      refineAddition.value = "";
      mediaStep.value = "";
      notify?.("正文已更新，确认后即以原图为参考出新版");
    } catch (e: unknown) {
      mediaError.value = e instanceof Error ? e.message : String(e);
      mediaStep.value = "";
    } finally {
      refineApplying.value = false;
    }
  }

  function closeRefine() {
    refineSource.value = null;
    refineAddition.value = "";
    refineDraft.value = null;
  }

  /**
   * 切回某一版：同条目其它版本自动取消选用，旧图不删。
   * 三处 selected 标记必须一起改——预览抽屉 / 详情画廊 / 大图各自读不同的副本，
   * 只 reload 列表会让当前选中框看起来"点了没反应"。
   */
  async function pickImage(image: MediaImageRecord, novel: string) {
    try {
      await selectMediaImage(image.id);
      const mark = (item: MediaImageRecord) => ({
        ...item,
        selected: item.id === image.id,
      });
      imagesByPrompt.value = {
        ...imagesByPrompt.value,
        [image.prompt_id]: (imagesByPrompt.value[image.prompt_id] ?? []).map(mark),
      };
      if (mediaPreview.value?.prompt_id === image.prompt_id) {
        mediaPreview.value = {
          ...mediaPreview.value,
          images: mediaPreview.value.images.map(mark),
        };
      }
      if (lightbox.value?.prompt_id === image.prompt_id) {
        lightbox.value = mark(lightbox.value);
      }
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
    activeReferenceId.value = "";
    closeRefine();
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
    refineSource,
    refineAddition,
    refineDraft,
    refineLoading,
    refineApplying,
    activeReferenceId,
    imagesOf,
    currentImage,
    loadImages,
    openMedia,
    confirmMedia,
    startRefine,
    buildRefineDraft,
    applyRefine,
    closeRefine,
    pickImage,
    showSidecar,
    closeMedia,
  };
}
