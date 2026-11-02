const DEFAULT_API_BASE = "/api"

function getApiHeaders(accessToken, csrfToken) {
  const headers = { "Content-Type": "application/json" }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`
  if (csrfToken) headers["X-CSRFToken"] = csrfToken
  return headers
}

async function readApiResponse(response) {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail =
      data.detail ||
      Object.values(data).flat().find((value) => typeof value === "string") ||
      `Upload request failed with HTTP ${response.status}.`
    const error = new Error(detail)
    error.status = response.status
    throw error
  }
  return data
}

function getResumeStorageKey(file, chapterId) {
  return `r2-multipart:v1:${encodeURIComponent(String(chapterId))}:${encodeURIComponent(file.name)}:${file.size}:${file.lastModified}`
}

export function findResumableUpload(file, chapterId) {
  if (!(file instanceof File) || !chapterId) return false
  return Boolean(localStorage.getItem(getResumeStorageKey(file, chapterId)))
}

async function postJson(url, headers, body, signal) {
  return readApiResponse(
    await fetch(url, {
      method: "POST",
      headers,
      credentials: "same-origin",
      signal,
      body: JSON.stringify(body),
    }),
  )
}

function uploadPart({ url, chunk, contentType, signal, onProgress }) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open("PUT", url)
    request.setRequestHeader("Content-Type", contentType)
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded)
    })
    request.addEventListener("load", () => {
      if (request.status < 200 || request.status >= 300) {
        reject(new Error(`R2 rejected an upload chunk with HTTP ${request.status}.`))
        return
      }
      const etag = request.getResponseHeader("ETag")
      if (!etag) {
        reject(
          new Error(
            "R2 did not expose the ETag response header. Check the bucket CORS configuration.",
          ),
        )
        return
      }
      resolve(etag)
    })
    request.addEventListener("error", () => {
      reject(new Error("The browser could not upload a chunk to R2. Check network and CORS."))
    })
    request.addEventListener("abort", () =>
      reject(new DOMException("Upload cancelled.", "AbortError")),
    )
    if (signal) {
      if (signal.aborted) {
        request.abort()
        return
      }
      signal.addEventListener("abort", () => request.abort(), { once: true })
    }
    request.send(chunk)
  })
}

export async function uploadVideoMultipart({
  file,
  chapterId,
  title = "",
  singleFormatOnly = false,
  outputFormat = "",
  apiBase = DEFAULT_API_BASE,
  accessToken,
  csrfToken,
  signal,
  onProgress = () => {},
  onCleanupError = () => {},
}) {
  if (!(file instanceof File)) throw new TypeError("Choose a video file to upload.")
  if (!chapterId) throw new TypeError("Choose a chapter before uploading.")
  if (singleFormatOnly && !outputFormat) {
    throw new TypeError("Choose an output format before uploading.")
  }

  const base = apiBase.replace(/\/+$/, "")
  const headers = getApiHeaders(accessToken, csrfToken)
  const storageKey = getResumeStorageKey(file, chapterId)

  try {
    onProgress({ phase: "starting", loaded: 0, total: file.size, percent: 0 })
    let savedUpload = JSON.parse(localStorage.getItem(storageKey) || "null")
    let uploadToken = savedUpload?.upload_token
    let resume
    if (uploadToken) {
      onProgress({ phase: "resuming", loaded: 0, total: file.size, percent: 0 })
      try {
        resume = await postJson(
          `${base}/videos/multipart/resume/`,
          headers,
          { upload_token: uploadToken },
          signal,
        )
      } catch (error) {
        if (error.status !== 400 && error.status !== 410) throw error
        localStorage.removeItem(storageKey)
        savedUpload = null
        uploadToken = null
      }
    }

    if (!uploadToken) {
      const initiated = await postJson(`${base}/videos/multipart/initiate/`, headers, {
          chapter_id: chapterId,
          filename: file.name,
          content_type: file.type,
          file_size: file.size,
          title,
          single_format_only: singleFormatOnly,
          output_format: singleFormatOnly ? outputFormat : "",
        }, signal)
      uploadToken = initiated.upload_token
      resume = {
        completed: false,
        parts: [],
        part_count: initiated.part_count,
        part_size: initiated.part_size,
      }
      savedUpload = {
        upload_token: uploadToken,
        filename: file.name,
        file_size: file.size,
        last_modified: file.lastModified,
        chapter_id: String(chapterId),
        created_at: Date.now(),
      }
      localStorage.setItem(storageKey, JSON.stringify(savedUpload))
    }

    if (resume.completed) {
      const result = await postJson(
        `${base}/videos/multipart/complete/`,
        headers,
        { upload_token: uploadToken, parts: [] },
        signal,
      )
      localStorage.removeItem(storageKey)
      onProgress({ phase: "complete", loaded: file.size, total: file.size, percent: 100 })
      return result
    }

    const uploadedParts = new Map()
    let completedBytes = 0
    for (const part of resume.parts) {
      const expectedSize = Math.min(
        resume.part_size,
        file.size - (part.PartNumber - 1) * resume.part_size,
      )
      if (
        part.PartNumber < 1 ||
        part.PartNumber > resume.part_count ||
        part.Size !== expectedSize ||
        !part.ETag
      ) {
        throw new Error(
          "Saved R2 chunks do not match this file. Select the original file or discard this upload.",
        )
      }
      uploadedParts.set(part.PartNumber, {
        PartNumber: part.PartNumber,
        ETag: part.ETag,
      })
      completedBytes += part.Size
    }
    onProgress({
      phase: "resuming",
      loaded: completedBytes,
      total: file.size,
      percent: Math.floor((completedBytes / file.size) * 100),
    })

    for (let partNumber = 1; partNumber <= resume.part_count; partNumber += 1) {
      if (uploadedParts.has(partNumber)) continue
      const start = (partNumber - 1) * resume.part_size
      const end = Math.min(start + resume.part_size, file.size)
      const chunk = file.slice(start, end)
      const signedPart = await postJson(
        `${base}/videos/multipart/part-url/`,
        headers,
        { upload_token: uploadToken, part_number: partNumber },
        signal,
      )
      const etag = await uploadPart({
        url: signedPart.url,
        chunk,
        contentType: file.type || "application/octet-stream",
        signal,
        onProgress: (partLoaded) => {
          const loaded = Math.min(completedBytes + partLoaded, file.size)
          onProgress({
            phase: "uploading",
            loaded,
            total: file.size,
            percent: Math.floor((loaded / file.size) * 100),
          })
        },
      })
      completedBytes += chunk.size
      uploadedParts.set(partNumber, { PartNumber: partNumber, ETag: etag })
      onProgress({
        phase: "uploading",
        loaded: completedBytes,
        total: file.size,
        percent: Math.floor((completedBytes / file.size) * 100),
      })
    }

    onProgress({ phase: "finalizing", loaded: file.size, total: file.size, percent: 100 })
    const parts = Array.from(uploadedParts.values()).sort(
      (left, right) => left.PartNumber - right.PartNumber,
    )
    const result = await postJson(
      `${base}/videos/multipart/complete/`,
      headers,
      { upload_token: uploadToken, parts },
      signal,
    )
    localStorage.removeItem(storageKey)
    onProgress({ phase: "complete", loaded: file.size, total: file.size, percent: 100 })
    return result
  } catch (error) {
    throw error
  }
}

export async function discardResumableVideoUpload({
  file,
  chapterId,
  apiBase = DEFAULT_API_BASE,
  accessToken,
  csrfToken,
}) {
  if (!(file instanceof File) || !chapterId) return
  const storageKey = getResumeStorageKey(file, chapterId)
  const savedUpload = JSON.parse(localStorage.getItem(storageKey) || "null")
  if (!savedUpload?.upload_token) return

  await postJson(
    `${apiBase.replace(/\/+$/, "")}/videos/multipart/abort/`,
    getApiHeaders(accessToken, csrfToken),
    { upload_token: savedUpload.upload_token },
  )
  localStorage.removeItem(storageKey)
}
