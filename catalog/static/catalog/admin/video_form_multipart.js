document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("#video_form")
  const fileInput = document.getElementById("videoInput")
  if (!form || !fileInput || fileInput.dataset.r2MultipartEnabled !== "true") return

  const fileRow = fileInput.closest(".form-row")
  const fileContainer = fileInput.closest(".flex-container")
  const fileHint = fileRow?.querySelector(".help")
  const uploadButton = document.createElement("button")
  uploadButton.id = "uploadBtn"
  uploadButton.type = "button"
  uploadButton.className = "button"
  uploadButton.textContent = "R2 ga yuklash"
  uploadButton.disabled = true
  fileContainer?.append(uploadButton)

  const dialog = document.createElement("dialog")
  dialog.className = "r2-upload-dialog"
  dialog.setAttribute("aria-labelledby", "r2-upload-title")
  dialog.innerHTML = `
    <h2 id="r2-upload-title">Videoni R2 ga yuklash</h2>
    <p data-upload-status role="status">Yuklashga tayyorlanmoqda...</p>
    <progress max="100" value="0" aria-label="Yuklash jarayoni"></progress>
    <div class="r2-upload-details">
      <span data-upload-percent>0%</span>
      <span data-upload-size>0 MB / 0 MB</span>
    </div>
    <p data-upload-error class="errornote" hidden></p>
    <p data-upload-resume hidden>Upload holati saqlandi. Shu faylni qayta tanlab davom ettirishingiz mumkin.</p>
    <div class="r2-upload-actions">
      <button type="button" class="button" data-upload-cancel>To‘xtatish va keyin davom ettirish</button>
      <button type="button" class="button" data-upload-discard hidden>Uploadni butunlay bekor qilish</button>
      <button type="button" class="default" data-upload-close hidden>Yopish</button>
    </div>
  `
  document.body.append(dialog)

  const progressBar = dialog.querySelector("progress")
  const statusText = dialog.querySelector("[data-upload-status]")
  const percentText = dialog.querySelector("[data-upload-percent]")
  const sizeText = dialog.querySelector("[data-upload-size]")
  const errorText = dialog.querySelector("[data-upload-error]")
  const resumeText = dialog.querySelector("[data-upload-resume]")
  const cancelButton = dialog.querySelector("[data-upload-cancel]")
  const discardButton = dialog.querySelector("[data-upload-discard]")
  const closeButton = dialog.querySelector("[data-upload-close]")
  const csrfToken = form.querySelector('[name="csrfmiddlewaretoken"]')?.value
  let controller
  let isUploading = false

  const formatMegabytes = (bytes) => `${(bytes / (1024 * 1024)).toFixed(2)} MB`

  fileInput.addEventListener("change", () => {
    uploadButton.disabled = fileInput.files.length === 0 || isUploading
    uploadButton.textContent = "R2 ga yuklash"
    resumeText.hidden = true
    if (fileInput.files[0]) {
      import(fileInput.dataset.r2ModuleUrl)
        .then(({ findResumableUpload }) => {
          if (findResumableUpload(fileInput.files[0], document.getElementById("id_chapter")?.value)) {
            uploadButton.textContent = "Yuklashni davom ettirish"
            resumeText.hidden = false
          }
        })
        .catch((error) => {
          uploadButton.disabled = true
          errorText.textContent = error.message || "Saqlangan upload holatini tekshirib bo‘lmadi."
          errorText.hidden = false
          console.error("Could not inspect saved multipart upload.", error)
        })
    }
  })

  const startUpload = async () => {
    const file = fileInput.files[0]
    if (!file || isUploading) return
    if (!form.reportValidity()) return

    const chapterId = document.getElementById("id_chapter")?.value
    if (!chapterId) {
      document.getElementById("id_chapter")?.reportValidity()
      return
    }

    isUploading = true
    uploadButton.disabled = true
    cancelButton.hidden = false
    closeButton.hidden = true
    discardButton.hidden = true
    errorText.hidden = true
    errorText.textContent = ""
    progressBar.value = 0
    percentText.textContent = "0%"
    sizeText.textContent = `0 MB / ${formatMegabytes(file.size)}`
    statusText.textContent = "R2 ga ulanish..."
    controller = new AbortController()
    if (!dialog.open) dialog.showModal()

    try {
      const { uploadVideoMultipart } = await import(fileInput.dataset.r2ModuleUrl)
      const result = await uploadVideoMultipart({
        file,
        chapterId,
        title: document.getElementById("id_title")?.value ?? "",
        singleFormatOnly: document.getElementById("id_single_format_only")?.checked ?? false,
        outputFormat: document.getElementById("id_output_format")?.value ?? "",
        csrfToken,
        signal: controller.signal,
        onProgress: ({ phase, loaded, total, percent }) => {
          progressBar.value = percent
          percentText.textContent = `${percent}%`
          sizeText.textContent = `${formatMegabytes(loaded)} / ${formatMegabytes(total)}`
          statusText.textContent =
            phase === "finalizing"
              ? "R2 yuklashni yakunlamoqda..."
              : phase === "complete"
                ? "Yuklandi. Video qayta ishlash navbatiga qo‘yildi."
                : phase === "uploading"
                  ? "Video R2 ga yuklanmoqda..."
                  : phase === "resuming"
                    ? "Saqlangan bo‘laklar tekshirilmoqda..."
                  : "R2 ga ulanish..."
        },
      })
      statusText.textContent = "Yuklandi. Video qayta ishlash navbatiga qo‘yildi."
      cancelButton.hidden = true
      discardButton.hidden = true
      closeButton.hidden = false
      closeButton.textContent = "Videolar ro‘yxatiga o‘tish"
      closeButton.addEventListener(
        "click",
        () => {
          const addPath = new URL(form.action, window.location.href)
          addPath.pathname = addPath.pathname.replace(/add\/?$/, `${result.id}/change/`)
          window.location.assign(addPath)
        },
        { once: true },
      )
    } catch (error) {
      if (error.name === "AbortError") {
        statusText.textContent = "Yuklash to‘xtatildi. Yuklangan bo‘laklar saqlandi."
        resumeText.hidden = false
      } else {
        statusText.textContent = "Yuklashda xatolik yuz berdi."
        errorText.textContent = error.message || "Noma’lum xatolik."
        errorText.hidden = false
      }
      console.error("Direct R2 video upload failed.", error)
      cancelButton.hidden = true
      discardButton.hidden = false
      closeButton.hidden = false
      closeButton.textContent = "Yopish"
    } finally {
      isUploading = false
      uploadButton.disabled = fileInput.files.length === 0
      controller = null
      if (fileInput.files[0]) {
        import(fileInput.dataset.r2ModuleUrl)
          .then(({ findResumableUpload }) => {
            if (findResumableUpload(fileInput.files[0], document.getElementById("id_chapter")?.value)) {
              uploadButton.textContent = "Yuklashni davom ettirish"
            }
          })
          .catch((error) => console.error("Could not inspect saved multipart upload.", error))
      }
    }
  }

  uploadButton.addEventListener("click", startUpload)
  form.addEventListener(
    "submit",
    (event) => {
      if (!fileInput.files.length) return
      event.preventDefault()
      event.stopImmediatePropagation()
      startUpload()
    },
    true,
  )

  cancelButton.addEventListener("click", () => {
    if (controller && isUploading) {
      statusText.textContent = "Yuklash to‘xtatilyapti, qismlar saqlanadi..."
      controller.abort()
      return
    }
    dialog.close()
  })
  discardButton.addEventListener("click", async () => {
    if (!fileInput.files[0]) return
    discardButton.disabled = true
    try {
      const { discardResumableVideoUpload } = await import(fileInput.dataset.r2ModuleUrl)
      await discardResumableVideoUpload({
        file: fileInput.files[0],
        chapterId: document.getElementById("id_chapter")?.value,
        csrfToken,
      })
      dialog.close()
      uploadButton.textContent = "R2 ga yuklash"
      resumeText.hidden = true
    } catch (error) {
      errorText.textContent = error.message || "Uploadni bekor qilib bo‘lmadi."
      errorText.hidden = false
      console.error("Could not discard saved multipart upload.", error)
    } finally {
      discardButton.disabled = false
    }
  })
  closeButton.addEventListener("click", () => dialog.close())

  const style = document.createElement("style")
  style.textContent = `
    .r2-upload-dialog { width: min(460px, calc(100vw - 32px)); border: 0; border-radius: 8px; padding: 24px; box-shadow: 0 12px 40px #0004; }
    .r2-upload-dialog::backdrop { background: #0008; }
    .r2-upload-dialog h2 { margin-top: 0; }
    .r2-upload-dialog progress { display: block; width: 100%; height: 18px; margin: 20px 0 8px; }
    .r2-upload-details, .r2-upload-actions { display: flex; justify-content: space-between; gap: 12px; }
    .r2-upload-actions { justify-content: flex-end; margin-top: 24px; }
    #uploadBtn { margin-left: 12px; }
  `
  document.head.append(style)

  if (fileHint) {
    fileHint.textContent += " R2 to‘g‘ridan-to‘g‘ri yuklash uchun “R2 ga yuklash” tugmasini bosing."
  }
})
