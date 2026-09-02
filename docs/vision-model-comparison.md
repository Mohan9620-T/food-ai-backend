# Vision model accuracy and performance comparison

Captured on 2026-09-02 with Ollama 0.33.2. This document reports Phase 1-2 data
only; model selection is intentionally deferred to Phase 3.

## Method

- Models: `llava:latest`, `qwen3-vl:4b`, and `qwen3-vl:8b`
- Images: the same two distinct persisted chat images and generated 2x2 red PNG
  used in the [LLaVA baseline](vision-baseline-llava.md)
- Prompt: `Describe what is visible in this image.`
- System prompt: the existing uncertainty-aware `ChatVisionService.SYSTEM_PROMPT`
- Options: `temperature=0`, `num_ctx=8192`
- Timing: wall-clock duration of the non-streaming Ollama `/api/chat` request
- Memory: peak Ollama container usage sampled during each request
- Docker memory limit: 7.518 GiB

The 8192-token context was required because Qwen3-VL encoded persisted image 2 as
4,234 tokens. Its default 4,096-token context returned HTTP 400 before inference.

## Comparison

| Model | Sample | Result | Time | Peak Ollama RAM | Accuracy observation |
|---|---|---:|---:|---:|---|
| `llava:latest` | Persisted image 1 | Success | 95.245 s | 6,719.49 MiB | Saw the banana leaf and condiments but called the idlis generic flatbread/pancakes. |
| `llava:latest` | Persisted image 2 | Success | 57.541 s | 6,056.96 MiB | Correctly identified a clownfish and aquarium. |
| `llava:latest` | Red fixture | Success | 48.779 s | 6,166.53 MiB | Correctly reported no identifiable objects, with cautious wording. |
| `qwen3-vl:4b` | Persisted image 1 | Success | 181.532 s | 5,582.85 MiB | Correctly identified six idlis and the South Indian setting; speculated about the orange item. |
| `qwen3-vl:4b` | Persisted image 2 | Success | 580.674 s | 6,371.33 MiB | Correctly identified the clownfish without LLaVA baseline's contradictory “Dory” claim. |
| `qwen3-vl:4b` | Red fixture | Success | 104.098 s | 6,809.60 MiB | Correctly reported no visible objects or food. |
| `qwen3-vl:8b` | Persisted image 1 | Success | 313.844 s | 6,945.79 MiB | Identified idlis, coconut chutney, and sambar, but counted five rather than 4b's six. |
| `qwen3-vl:8b` | Persisted image 2 | **Failed** | 1.865 s before failure | Not retained | Ollama returned HTTP 500 `unexpected EOF` while encoding the 4,234-token image. |
| `qwen3-vl:8b` | Red fixture | Success | 216.722 s | 7,001.09 MiB | Correctly reported a uniform red image with no visible objects or food. |

## System impact

The 8b model caused clear system pressure and slowdown. A live snapshot during its
first request showed 6.553 GiB of 7.518 GiB (87.17%) in the Ollama container and
688.68% CPU. Its recorded peaks reached 7,001.09 MiB, about 91% of Docker's memory
limit. The larger second image then failed during multimodal encoding with
`unexpected EOF`. This makes the 8b results incomplete by behavior of the model on
this machine, not by omission from the benchmark.

The 4b model used less memory on its first food image and delivered materially more
specific food recognition than LLaVA, but it was substantially slower. Its large
image took 9 minutes 41 seconds and its red fixture took 1 minute 44 seconds.

## Raw responses

### LLaVA — persisted image 1

> The image shows a plate of food on a table. There are several round, white,
> bread-like items that appear to be some kind of flatbread or pancake. Next to the
> bread, there is a small bowl of a green paste, which could be a type of chutney or
> condiment. There is also a small amount of a red substance, which might be a type
> of curry or sauce. The food is presented on a banana leaf, which is a common way
> to serve food in some cultures. The background is blurred, but it seems to be an
> indoor setting with a warm, inviting atmosphere. There is no visible text in the
> image.

### LLaVA — persisted image 2

> The image shows a vibrant clownfish swimming in an aquarium. The clownfish is
> bright orange with white stripes and black spots, and it has a large, colorful
> dorsal fin. The fish is swimming near the bottom of the aquarium, which is filled
> with clear water. The background is slightly blurred, but it appears to be a
> darker area, possibly indicating the depth of the aquarium. There are no visible
> texts or other objects in the image.

### LLaVA — red fixture

> The image is a close-up of a red surface, which appears to be a table or a counter.
> There are no visible objects, people, or actions that can be confidently described.
> The image is too blurry and lacks detail to provide any information about the scene.

### Qwen3-VL 4b — persisted image 1

> The image shows a **green banana leaf** serving as the base for a traditional South Indian meal.
>
> ### Visible Food Items:
> - **6 round, white idlis** (steamed rice-lentil cakes) arranged in a cluster on the left side of the leaf. They appear soft and slightly porous, typical of idlis.
> - **A small mound of greenish paste** (likely a chutney or sambar) on the upper right. It has a textured, slightly chunky appearance with hints of light brown or beige.
> - **An orange, textured item** (possibly a curry or a type of dosa) at the bottom right. It has a rough, uneven surface with dark spots (which could be spices, lentils, or other ingredients).
>
> ### Other Details:
> - The banana leaf is vibrant green with visible veins, a common traditional serving surface for South Indian dishes.
> - No text or labels are clearly visible in the image.
>
> ### Context:
> This setup is typical of a South Indian breakfast or snack, where idlis are often served with chutney or sambar. The portions appear to be standard for a single serving (e.g., 6 idlis, a small portion of chutney, and a modest amount of the orange item).
>
> Would you like to know more about the ingredients or nutritional details of these specific items? (Note: I won’t log or assume the meal, but I can share general info if you’re curious!)

### Qwen3-VL 4b — persisted image 2

> This image shows a **clownfish** (a type of marine fish) swimming in what appears to be an aquarium or underwater environment. The fish has the characteristic bright orange body with bold white stripes bordered by black lines—typical of the *Amphiprion* genus, like the common clownfish (*Amphiprion ocellaris*).
>
> ### Key details:
> - **Fish**: The clownfish is the central focus, with its vibrant orange, white, and black coloration clearly visible. It’s mid-swim, facing right, with fins spread slightly.
> - **Background**: Blurred, soft-focus elements suggest an aquatic setting—likely greenish water with hints of brownish seaweed or rock formations (the horizontal brownish band at the top might be a piece of coral or artificial decor).
> - **No food or drink items**: There are no visible food, beverages, or culinary items in the image.
>
> ### Additional notes:
> - No text is present in the image.
> - The scene is purely aquatic; there are no people, utensils, or other objects related to meal preparation or consumption.
>
> Since there’s no food or drink visible, I don’t see anything to log or analyze nutritionally. If you have a different image with food items, I’d be happy to help describe them! 😊

### Qwen3-VL 4b — red fixture

> This image is a solid, uniform red color with no discernible objects, people, text,
> or details visible. There are no visible food items, drinks, or other elements to
> describe.

### Qwen3-VL 8b — persisted image 1

> The image shows a traditional South Indian meal served on a fresh green banana leaf. Here’s what’s visible:
>
> - **Idlis**: Five round, white steamed cakes made from fermented rice and urad dal batter (a common South Indian breakfast/dinner staple). They’re arranged in a loose cluster on the left side of the leaf.
> - **Coconut Chutney**: A small mound of greenish, textured paste on the right side of the leaf. This is typically made from grated coconut, tempered spices, and sometimes tamarind or jaggery.
> - **Sambar**: A small portion of orange-brown, chunky stew with visible black specks (likely lentils or spices). Sambar is a lentil-based vegetable stew, often made with toor dal, vegetables, and spices.
>
> No text is visible in the image. The banana leaf serves as the plate, which is typical for South Indian meals.
>
> Would you like to log this meal? (I can’t confirm it’s been logged, but I can help you track it if you’d like!)

### Qwen3-VL 8b — persisted image 2

> `{"error":"an error was encountered while running the model: unexpected EOF"}`

### Qwen3-VL 8b — red fixture

> The image shows a solid, uniform red background with no visible objects, people,
> text, or food items. There are no discernible details beyond the red color filling
> the entire frame.

## Conclusion and recommendation

Use **`qwen3-vl:4b`** for both meal-image parsing and general image chat. It delivered
the clearest material accuracy improvement on the food sample by identifying six idlis
and the South Indian context. Its latency is high and must be addressed through bounded
output and production timeout/residency tuning in later phases, but it completed every
sample without exhausting Docker's memory allocation.

Do not use `qwen3-vl:8b` on this machine. Its accuracy was not meaningfully better than
4b, it counted fewer idlis, used about 91% of Docker's 7.518 GiB memory limit, and failed
the higher-context image with `unexpected EOF`. LLaVA remains faster, but its generic
food identification misses the project's core regional-food use case.
