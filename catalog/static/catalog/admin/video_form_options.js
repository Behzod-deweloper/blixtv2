const initializeVideoFormOptions = () => {
  const comicSelect = document.getElementById('id_comic')
  const chapterSelect = document.getElementById('id_chapter')
  const recentComicsOnly = document.getElementById('id_recent_comics_only')
  const singleFormatOnly = document.getElementById('id_single_format_only')
  const outputFormat = document.getElementById('id_output_format')
  const comicOptions = comicSelect ? Array.from(comicSelect.options) : []

  if (singleFormatOnly && outputFormat) {
    const singleFormatRow = singleFormatOnly.closest('.form-row')
    const outputFormatRow = outputFormat.closest('.form-row')

    if (singleFormatRow && outputFormatRow) {
      const updateOutputFormatVisibility = () => {
        outputFormatRow.hidden = !singleFormatOnly.checked
        outputFormat.disabled = !singleFormatOnly.checked
      }

      singleFormatOnly.addEventListener('change', updateOutputFormatVisibility)
      updateOutputFormatVisibility()
    }
  }

  if (!comicSelect || !chapterSelect) return

  const originalFileRow = document.getElementById('videoInput')?.closest('.form-row')
  const singleFormatRow = singleFormatOnly?.closest('.form-row')
  const outputFormatRow = outputFormat?.closest('.form-row')
  if (originalFileRow && singleFormatRow && outputFormatRow) {
    originalFileRow.after(singleFormatRow)
    singleFormatRow.after(outputFormatRow)
  }

  if (recentComicsOnly) {
    const comicContainer = comicSelect.closest('.flex-container')
    const recentRow = recentComicsOnly.closest('.form-row')
    const recentCheckbox = recentRow
      ? recentRow.querySelector('.flex-container.checkbox-row')
      : null

    if (comicContainer && recentRow && recentCheckbox) {
      const selectStack = document.createElement('div')
      selectStack.className = 'recent-comics-select-stack'
      recentCheckbox.classList.add('recent-comics-filter')
      comicContainer.insertBefore(selectStack, comicSelect)
      selectStack.append(comicSelect, recentCheckbox)
      recentRow.hidden = true
    }
  }

  const filterChapters = (resetSelection) => {
    const comicId = comicSelect.value
    let latestChapterValue = ''

    for (const option of chapterSelect.options) {
      const matchesComic = option.value && option.dataset.comicId === comicId
      option.hidden = Boolean(option.value && !matchesComic)
      option.disabled = Boolean(option.value && !matchesComic)
      if (matchesComic && option.dataset.latestChapter === 'true') {
        latestChapterValue = option.value
      }
    }

    if (resetSelection) chapterSelect.value = latestChapterValue
    chapterSelect.disabled = !comicId
  }

  const filterRecentComics = () => {
    if (!recentComicsOnly) return

    const showRecentOnly = recentComicsOnly.checked
    const selectedComicId = comicSelect.value
    const visibleOptions = []
    for (const option of comicOptions) {
      const isRecent = option.dataset.recentComic === 'true'
      const isSelected = option.value === selectedComicId
      if (!option.value || !showRecentOnly || isRecent || isSelected) {
        visibleOptions.push(option)
      }
    }
    comicSelect.replaceChildren(...visibleOptions)
    comicSelect.value = selectedComicId
  }

  comicSelect.addEventListener('change', () => {
    filterChapters(true)
    filterRecentComics()
  })
  if (recentComicsOnly) {
    recentComicsOnly.addEventListener('change', filterRecentComics)
    filterRecentComics()
  }
  filterChapters(false)
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeVideoFormOptions, { once: true })
} else {
  initializeVideoFormOptions()
}
