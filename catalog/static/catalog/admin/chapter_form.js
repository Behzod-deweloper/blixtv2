document.addEventListener("DOMContentLoaded", () => {
  const comicSelect = document.getElementById("id_comic")
  const numberInput = document.getElementById("id_number")
  const recentComicsOnly = document.getElementById("id_recent_comics_only")
  if (!comicSelect) return

  if (recentComicsOnly) {
    const comicContainer = comicSelect.closest(".flex-container")
    const recentRow = recentComicsOnly.closest(".form-row")
    const recentCheckbox = recentRow
      ? recentRow.querySelector(".flex-container.checkbox-row")
      : null

    if (comicContainer && recentRow && recentCheckbox) {
      const selectStack = document.createElement("div")
      selectStack.className = "recent-comics-select-stack"
      recentCheckbox.classList.add("recent-comics-filter")
      comicContainer.insertBefore(selectStack, comicSelect)
      selectStack.append(comicSelect, recentCheckbox)
      recentRow.hidden = true
    }
  }

  const setNextChapterNumber = (force) => {
    const option = comicSelect.selectedOptions[0]
    const nextNumber = option ? option.dataset.nextChapterNumber : null
    if (numberInput && nextNumber && (force || !numberInput.value)) {
      numberInput.value = nextNumber
    }
  }

  const filterRecentComics = () => {
    if (!recentComicsOnly) return

    const showRecentOnly = recentComicsOnly.checked
    const selectedComicId = comicSelect.value
    for (const option of comicSelect.options) {
      const isRecent = option.dataset.recentComic === "true"
      const isSelected = option.value === selectedComicId
      const isHidden = Boolean(option.value && showRecentOnly && !isRecent && !isSelected)
      option.hidden = isHidden
      option.disabled = isHidden
    }
  }

  comicSelect.addEventListener("change", () => {
    setNextChapterNumber(true)
    filterRecentComics()
  })
  if (recentComicsOnly) {
    recentComicsOnly.addEventListener("change", filterRecentComics)
    filterRecentComics()
  }
  setNextChapterNumber(false)
})
