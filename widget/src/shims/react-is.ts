import React from 'react';

export const isFragment = (value: unknown): boolean => {
  if (!React.isValidElement(value)) {
    return false;
  }
  return value.type === React.Fragment;
};

