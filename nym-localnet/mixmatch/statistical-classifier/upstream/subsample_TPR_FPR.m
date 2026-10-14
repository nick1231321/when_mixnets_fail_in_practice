function [subsampled_FPR, subsampled_TPR] = subsample_TPR_FPR(FPR, TPR, samples_count)
  m = min(FPR(FPR>0));
  a = (log(m))/(-(samples_count-1));
  b = samples_count*log(m)/(samples_count-1);
  target_FPR = exp([1:500]*a+b);
  subsampled_TPR = zeros(samples_count,1);
  subsampled_FPR = zeros(samples_count,1);
  for k=1:samples_count
    index = find(abs(FPR - target_FPR(k)) == min(abs(FPR - target_FPR(k))), 1);
    subsampled_TPR(k) = TPR(index);
    subsampled_FPR(k) = FPR(index);
    fprintf(1, '\rTPR and FPR sample %i computed.', k);
    fflush(1);
  endfor
  fprintf(1, '\n');
endfunction
