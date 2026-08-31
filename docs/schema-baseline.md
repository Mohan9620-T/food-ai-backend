# SQLAlchemy Schema Baseline

This reference was generated from the SQLAlchemy model definitions in `app/models/`.

| Model | Table | Column | SQLAlchemy type | Constraints and defaults |
| --- | --- | --- | --- | --- |
| `User` | `users` | `id` | `Integer` | Primary key; indexed |
| `User` | `users` | `fullname` | `String(100)` | Not nullable |
| `User` | `users` | `email` | `String(150)` | Unique; not nullable |
| `User` | `users` | `password` | `String(255)` | Not nullable |
| `ChatSession` | `chat_sessions` | `id` | `Integer` | Primary key; indexed |
| `ChatSession` | `chat_sessions` | `user_id` | `Integer` | Foreign key to `users.id`; not nullable; indexed |
| `ChatSession` | `chat_sessions` | `title` | `String(255)` | Not nullable; Python-side default `"New chat"` |
| `ChatSession` | `chat_sessions` | `created_at` | `DateTime` | Python-side default: current UTC time |
| `ChatSession` | `chat_sessions` | `updated_at` | `DateTime` | Python-side default: current UTC time; updated to current UTC time on change |
| `ChatMessageRecord` | `chat_messages` | `id` | `Integer` | Primary key; indexed |
| `ChatMessageRecord` | `chat_messages` | `session_id` | `Integer` | Foreign key to `chat_sessions.id`; not nullable; indexed |
| `ChatMessageRecord` | `chat_messages` | `sender` | `String(10)` | Not nullable; application values documented as `"user"` or `"bot"` |
| `ChatMessageRecord` | `chat_messages` | `content` | `Text` | Not nullable |
| `ChatMessageRecord` | `chat_messages` | `created_at` | `DateTime` | Python-side default: current UTC time |
| `RefreshToken` | `refresh_tokens` | `id` | `Integer` | Primary key; indexed |
| `RefreshToken` | `refresh_tokens` | `user_id` | `Integer` | Foreign key to `users.id`; not nullable; indexed |
| `RefreshToken` | `refresh_tokens` | `token_hash` | `String(64)` | Unique; not nullable; SHA-256 hash only |
| `RefreshToken` | `refresh_tokens` | `expires_at` | `DateTime(timezone=True)` | Not nullable |
| `RefreshToken` | `refresh_tokens` | `revoked_at` | `DateTime(timezone=True)` | Nullable |
| `RefreshToken` | `refresh_tokens` | `created_at` | `DateTime(timezone=True)` | Not nullable; Python-side default: current UTC time |
| `MealLog` | `meal_logs` | `id` | `Integer` | Primary key; indexed |
| `MealLog` | `meal_logs` | `user_id` | `Integer` | Foreign key to `users.id`; not nullable; indexed |
| `MealLog` | `meal_logs` | `raw_description` | `Text` | Not nullable |
| `MealLog` | `meal_logs` | `source` | `String(10)` | Not nullable; defaults to `"text"`; application values are `"text"` or `"image"` |
| `MealLog` | `meal_logs` | `logged_at` | `DateTime(timezone=True)` | Not nullable; included in user/date composite index |
| `MealLog` | `meal_logs` | `created_at` | `DateTime(timezone=True)` | Not nullable; Python-side default: current UTC time |
| `MealLogItem` | `meal_log_items` | `id` | `Integer` | Primary key; indexed |
| `MealLogItem` | `meal_log_items` | `meal_log_id` | `Integer` | Foreign key to `meal_logs.id`; not nullable; indexed |
| `MealLogItem` | `meal_log_items` | `food_name` | `String(255)` | Not nullable |
| `MealLogItem` | `meal_log_items` | `quantity` | `Float` | Not nullable |
| `MealLogItem` | `meal_log_items` | `unit` | `String(50)` | Not nullable |
| `MealLogItem` | `meal_log_items` | `fdc_id` | `Integer` | Nullable; indexed; null means unmatched |
| `MealLogItem` | `meal_log_items` | `calories` | `Float` | Nullable; USDA-derived only |
| `MealLogItem` | `meal_log_items` | `protein_g` | `Float` | Nullable; USDA-derived only |
| `MealLogItem` | `meal_log_items` | `carbs_g` | `Float` | Nullable; USDA-derived only |
| `MealLogItem` | `meal_log_items` | `fat_g` | `Float` | Nullable; USDA-derived only |

## ORM relationships

| Model | Relationship | Target | Configuration |
| --- | --- | --- | --- |
| `ChatSession` | `messages` | `ChatMessageRecord` | `back_populates="session"`; cascade `all, delete-orphan`; ordered by `ChatMessageRecord.created_at` |
| `ChatMessageRecord` | `session` | `ChatSession` | `back_populates="messages"` |
| `User` | `refresh_tokens` | `RefreshToken` | `back_populates="user"`; cascade `all, delete-orphan` |
| `RefreshToken` | `user` | `User` | `back_populates="refresh_tokens"` |
| `User` | `meal_logs` | `MealLog` | cascade `all, delete-orphan` |
| `MealLog` | `items` | `MealLogItem` | `back_populates="meal_log"`; cascade `all, delete-orphan`; ordered by item ID |
| `MealLogItem` | `meal_log` | `MealLog` | `back_populates="items"` |
