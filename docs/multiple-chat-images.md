# Multiple chat images

The composer accepts up to five JPEG, PNG, WebP, or GIF images per message, using
the file picker, drag and drop, or clipboard image paste (Ctrl+V). Images are
appended to the current draft and can be removed individually. Ordinary text
paste is unchanged. The limit is 8 MB per image and 20 MB for the entire batch.
Documents remain a single-file upload.

`POST /chat/vision` accepts repeated multipart `images` fields. The legacy
single `image` field remains supported. Every file is validated before inference;
one invalid file rejects the batch. All images reach the vision provider in upload
order in the same request, with image-number guidance for comparisons.

The first image retains its existing database storage. Migration
`0016_chat_image_attachments` adds storage for the remaining images, linked to
their user message and deleted with that message. Session history returns
`image_urls` in upload order and retains the legacy `image_url`. Image follow-ups
reuse the entire selected group, including after reopening the chat.

Validation includes multipart order, invalid batches, size limits, session
ownership, saved history, deletion, provider payloads, composer paste, browser
upload/send/reload on desktop and mobile, and accessibility checks. A live NVIDIA
service check with five different colored images returned all five colors in order.
