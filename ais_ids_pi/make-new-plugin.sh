#! /bin/bash

# This script refactors this plugin into a new plugin with a name of your choice.
# To rename the plugin to "catsarebest", run `bash make-new-plugin.sh catsarebest`.

newname="$1"
Newname="$(echo "${newname^}")"
NEWNAME="$(echo "${newname^^}")"

grep -rl ais_ids . | grep -v .git | while read name; do
  sed -e "s+ais_ids+$newname+g" -i "$name";
done

grep -rl Ais_ids . | grep -v .git | while read name; do  
  sed -e "s+Ais_ids+$Newname+g" -i "$name";
done 

grep -rl AIS_IDS . | grep -v .git | while read name; do  
  sed -e "s+AIS_IDS+$NEWNAME+g" -i "$name";
done 

find . -name "*ais_ids*" | grep -v .git | while read name; do
  mv "$name" "$(echo "$name" | sed -e "s+ais_ids+$newname+g")"
done

find . -name "*Ais_ids*" | grep -v .git | while read name; do
  mv "$name" "$(echo "$name" | sed -e "s+Ais_ids+$Newname+g")"
done
