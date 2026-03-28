const DEFAULT_SURFACE_WIDTH_PX: u32 = 3840;
const DEFAULT_SURFACE_HEIGHT_PX: u32 = 1024;
const DEFAULT_HORIZONTAL_PADDING_PX: u32 = 96;
const DEFAULT_VERTICAL_PADDING_PX: u32 = 64;
const DEFAULT_LINE_HEIGHT_PX: u32 = 88;
const DEFAULT_BLOCK_SPACING_PX: u32 = 32;
const DEFAULT_AVERAGE_GLYPH_ADVANCE_PX: u32 = 32;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptionBlock {
    pub id: String,
    pub text: String,
}

impl CaptionBlock {
    pub fn new(id: impl Into<String>, text: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            text: text.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VisibleCaptionBlock {
    pub id: String,
    pub lines: Vec<String>,
    pub truncated: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptionLayoutResult {
    pub visible_blocks: Vec<VisibleCaptionBlock>,
    pub dropped_block_ids: Vec<String>,
    pub surface_width_px: u32,
    pub surface_height_px: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptionLayoutPolicy {
    preferred_weights: [&'static str; 3],
    latin_face_chain: [&'static str; 3],
    cjk_face_chain: [&'static str; 10],
    visible_window_target_blocks: usize,
    horizontal_padding_px: u32,
    vertical_padding_px: u32,
    line_height_px: u32,
    block_spacing_px: u32,
    average_glyph_advance_px: u32,
}

impl Default for CaptionLayoutPolicy {
    fn default() -> Self {
        Self {
            preferred_weights: ["Semibold", "Medium", "Regular"],
            latin_face_chain: ["Noto Sans", "Segoe UI", "DirectWrite system fallback"],
            cjk_face_chain: [
                "Noto Sans CJK KR",
                "Noto Sans CJK JP",
                "Noto Sans CJK SC",
                "Noto Sans CJK TC",
                "Malgun Gothic",
                "Yu Gothic UI",
                "Microsoft YaHei UI",
                "Microsoft JhengHei UI",
                "Segoe UI",
                "DirectWrite system fallback",
            ],
            visible_window_target_blocks: 2,
            horizontal_padding_px: DEFAULT_HORIZONTAL_PADDING_PX,
            vertical_padding_px: DEFAULT_VERTICAL_PADDING_PX,
            line_height_px: DEFAULT_LINE_HEIGHT_PX,
            block_spacing_px: DEFAULT_BLOCK_SPACING_PX,
            average_glyph_advance_px: DEFAULT_AVERAGE_GLYPH_ADVANCE_PX,
        }
    }
}

impl CaptionLayoutPolicy {
    pub fn preferred_weights(&self) -> Vec<&'static str> {
        self.preferred_weights.to_vec()
    }

    pub fn latin_face_chain(&self) -> &[&'static str] {
        &self.latin_face_chain
    }

    pub fn cjk_face_chain(&self) -> &[&'static str] {
        &self.cjk_face_chain
    }

    pub fn visible_window_target_blocks(&self) -> usize {
        self.visible_window_target_blocks
    }

    pub fn default_surface_size(&self) -> (u32, u32) {
        (DEFAULT_SURFACE_WIDTH_PX, DEFAULT_SURFACE_HEIGHT_PX)
    }

    pub fn layout_blocks(
        &self,
        blocks: Vec<CaptionBlock>,
        surface_width_px: u32,
        surface_height_px: u32,
    ) -> CaptionLayoutResult {
        let available_height_px = surface_height_px
            .saturating_sub(self.vertical_padding_px.saturating_mul(2))
            .max(self.line_height_px);
        let max_chars_per_line = self.max_chars_per_line(surface_width_px);

        let mut visible_newest_first = Vec::new();
        let mut dropped_block_ids = Vec::new();
        let mut used_height_px = 0;

        for block in blocks.into_iter().rev() {
            let wrapped_lines = wrap_text(&block.text, max_chars_per_line);
            let block_height_px = self.block_height_px(wrapped_lines.len());
            let spacing_px = if visible_newest_first.is_empty() {
                0
            } else {
                self.block_spacing_px
            };

            if used_height_px + spacing_px + block_height_px <= available_height_px {
                used_height_px += spacing_px + block_height_px;
                visible_newest_first.push(VisibleCaptionBlock {
                    id: block.id,
                    lines: wrapped_lines,
                    truncated: false,
                });
                continue;
            }

            if !visible_newest_first.is_empty() {
                dropped_block_ids.push(block.id);
                continue;
            }

            let max_lines = (available_height_px / self.line_height_px).max(1) as usize;
            let truncated = wrapped_lines.len() > max_lines;
            visible_newest_first.push(VisibleCaptionBlock {
                id: block.id,
                lines: wrapped_lines.into_iter().take(max_lines).collect(),
                truncated,
            });
            used_height_px = available_height_px;
        }

        visible_newest_first.reverse();

        CaptionLayoutResult {
            visible_blocks: visible_newest_first,
            dropped_block_ids,
            surface_width_px,
            surface_height_px,
        }
    }

    fn max_chars_per_line(&self, surface_width_px: u32) -> usize {
        let available_width_px = surface_width_px
            .saturating_sub(self.horizontal_padding_px.saturating_mul(2))
            .max(self.average_glyph_advance_px);
        (available_width_px / self.average_glyph_advance_px).max(1) as usize
    }

    fn block_height_px(&self, line_count: usize) -> u32 {
        (line_count.max(1) as u32).saturating_mul(self.line_height_px)
    }
}

fn wrap_text(text: &str, max_chars_per_line: usize) -> Vec<String> {
    let mut lines = Vec::new();

    for paragraph in text.lines() {
        let words: Vec<&str> = paragraph.split_whitespace().collect();
        if words.is_empty() {
            lines.push(String::new());
            continue;
        }

        let mut current = String::new();
        for word in words {
            if current.is_empty() {
                push_word_chunks(&mut lines, &mut current, word, max_chars_per_line);
                continue;
            }

            let candidate_len = current.chars().count() + 1 + word.chars().count();
            if candidate_len <= max_chars_per_line {
                current.push(' ');
                current.push_str(word);
                continue;
            }

            lines.push(std::mem::take(&mut current));
            push_word_chunks(&mut lines, &mut current, word, max_chars_per_line);
        }

        if !current.is_empty() {
            lines.push(current);
        }
    }

    if lines.is_empty() {
        lines.push(String::new());
    }

    lines
}

fn push_word_chunks(
    lines: &mut Vec<String>,
    current: &mut String,
    word: &str,
    max_chars_per_line: usize,
) {
    let chars: Vec<char> = word.chars().collect();
    if chars.len() <= max_chars_per_line {
        current.push_str(word);
        return;
    }

    for chunk in chars.chunks(max_chars_per_line) {
        let piece: String = chunk.iter().collect();
        if current.is_empty() {
            lines.push(piece);
        } else {
            lines.push(std::mem::take(current));
            lines.push(piece);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::wrap_text;

    #[test]
    fn wrap_text_splits_long_words_into_fixed_width_chunks() {
        let lines = wrap_text("abcdefgh", 3);
        assert_eq!(lines, vec!["abc", "def", "gh"]);
    }
}
