#!/usr/bin/env bash
set -euo pipefail

# Список путей вида "<id>/<filename>"
files=(
  "44017/rewiew_pif_aif_23Q1"
  "46283/rewiew_pif_aif_23Q2"
  "46625/rewiew_uk_23Q3"
  "48954/rewiew_uk_23Q4"
  "49208/rewiew_uk_24Q1"
  "50577/rewiew_uk_24Q2"
  "54852/rewiew_uk_24Q3"
  "55183/rewiew_uk_24Q4"
  "55935/rewiew_uk_25Q1"
  "57205/rewiew_uk_25Q2"
  "59449/rewiew_uk_25Q3"
  "59736/rewiew_uk_25Q4"
  "62033/rewiew_uk_26Q1"
  "62329/rewiew_uk_26Q2"
)

base_url="https://cbr.ru/Collection/Collection/File"
out_dir="cbr_reviews"
mkdir -p "$out_dir"

for f in "${files[@]}"; do
  url="${base_url}/${f}.pdf"
  # имя файла без префикса с ID
  name="${f##*/}.pdf"
  echo "→ Скачиваю: $name"
  wget -q --show-progress -O "${out_dir}/${name}" "$url"
done

echo "Готово. Файлы в папке: ${out_dir}/"