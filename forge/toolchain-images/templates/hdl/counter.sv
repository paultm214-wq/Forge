module counter(input clk,input reset,output reg [3:0] count);always @(posedge clk) if(reset) count<=0;else count<=count+1;endmodule
